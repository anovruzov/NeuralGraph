# The power check: could the audit have seen the signal at all?

**Synthetic data only.** Every number on this page comes from generated worlds whose plants were written by the same
author as the detectors. It measures the instrument, not the world.

The vehicle replays (V001 to V003) found no early warning beyond chance. Nobody had first asked whether the detectors
could find a signal of the size the complaint data carry. DIAG-E001 later measured that size: complaints matching an
investigation rise to about 1.75 a quarter, roughly 0.1 a week, in the quarter before it opens. The power check asks the
question before a replay, and the rule in `PILOT.md` makes a passing check a condition of pre-registering one.

```
python -m mycelic.collective.pilot.power run --pack <pack> --background <background file> --out runs/power/<id> \
    [--rates 0.1,0.5,2.0] [--years 2,4] [--seeds 1,2,3] [--lookback-weeks 26,104] [--workers 3]
```

It exits 0 when the gate passes, 1 when it fails, 2 on a usage error. The code is
`mycelic/collective/pilot/power.py`; the vehicle background is built by `tools/market/vehicle_power.py`.

## How it works

**The background** (`power/vehicle-background.json`, sha256 `20c7401b...2ace41`). Each world draws its own entity
rates, then generates weekly records. The numbers and where they come from:

| Setting | Value | Source |
|---|---|---|
| Records a week | 262.35 | V001 run 002: 27,397 complaints on one make in 731 days |
| Sites | 60 | V001: 60 states and territories |
| Vehicles | 1,409 | DIAG-E001 run 2: 332,225 complaints on 3,420 vehicles in 10 years, 0.1861 a week each |
| Failure shares | 27 categories | `nhtsa-probe.json` component counts; categories the pack lacks count as unknown |
| Ramp vehicles | 0.368 to 0.671 records a week | DIAG-E001: investigations' matching complaints over their matching share |
| Sextupling keys | 0.074 to 0.174 records a week | DIAG-E001: recalls' matching complaints, quarters -12 to -1 |

Assumptions, named as such: vehicle rates follow a gamma with shape 0.5 (the diagnostic gives only the mean); site
weights fall as 1 / rank (no state shares are recorded); rates are flat over time; each complaint has one component;
each narrative names its component, so X reads the same failures as S.

**The plants.** Each world carries 10 ramps at each rate and 10 sextuplings, every one on its own vehicle:
- a **ramp** adds records on one vehicle and failure, rising linearly from 0 to the rate a week over the two years
  before the outcome opens. In a 2-year world it is cut at the export's first week. In runs 001 and 002 a ramp at 0.1
  a week added 5.0 to 5.6 records on average, and one at 2.0 added 102.7 to 104.1;
- a **sextupling** runs one vehicle and failure at six times its own rate for the 13 weeks before the outcome.

Each plant has a **control**: an unplanted vehicle and failure from the same band, with an outcome opened the same day.
Outcomes open in the last 26 weeks of the export. Each world goes through `pilot.audit` unchanged, with all five
channels: X, S and model-free R split by state, and the two comparators, P (pooled national) and PRR
(disproportionality). Every channel walks the export from its first week. Alerts before the first evaluated week
(week index 19, after the detectors' 12 weeks of history and 8-week window) are dropped.

**Power** is the share of plants with an alert on their own vehicle and failure, available in the look-back before the
outcome opens. The **control share** is the same share over the controls. **The gate**, per ramp rate and history: the
best channel must find at least 80% of plants, counting only channels that find at most 20% of controls.

## Run 002: the vehicle-like background

Command, run in this sandbox on commit `bb4e342` (this branch after merging the main branch). The commits after it
change only documents.

```
python -m mycelic.collective.pilot.power run --pack docs/collective/replay/vehicles/pack \
    --background docs/collective/power/vehicle-background.json --out <dir> --lookback-weeks 26,104 --workers 3
```

Six worlds (2 and 4 years, seeds 1 to 3), 30 plants and 30 controls per cell, 481 seconds with 3 workers. Each 2-year
world held 28,721 to 28,939 records; each 4-year world 55,969 to 56,327. The full output is `power/grid-002.json`
(sha256 `e1a0af25...803083`) and its report `power/grid-002.md`. The command exited 1: the gate failed.

**Run 001, and what changed.** Run 001 (`power/grid-001.json`, sha256 `8902fbda...5c29cf`) ran the same command on
the work that became commits `1c069f2` and `f837670`. Its PRR walked from the first evaluated week with nothing
cooling. So a failure already disproportionate when the export began alerted in the first evaluated weeks, as if it
were new. Commit `e02b8fe` walks PRR from the export's first week, as the detectors walk, and drops its alerts before
the first evaluated week, as for every channel. Run 002 is the same grid after that fix. The records, the alert counts
and every X, S, R_mf and P result are the same in both runs, plant by plant and control by control. 46 PRR results
changed, all in the 2-year worlds with the 104-week look-back. Run 001 is kept as it ran; its PRR numbers are
superseded.

**Ramps, 26-week look-back** (the replays' setting). Plants found, of 30:

| Rate a week | Years | X | S | R_mf | P | PRR |
|---|---|---|---|---|---|---|
| 0.1 | 2 | 0 | 0 | 0 | 4 | 6 |
| 0.1 | 4 | 0 | 0 | 0 | 6 | 7 |
| 0.5 | 2 | 0 | 0 | 1 | 3 | 2 |
| 0.5 | 4 | 1 | 1 | 4 | 2 | 2 |
| 2.0 | 2 | 13 | 13 | 20 | 3 | 0 |
| 2.0 | 4 | 9 | 9 | 15 | 2 | 0 |

No cell passes. The best is R_mf at 2.0 a week over two years: 20 of 30 (0.67, 95% interval 0.49 to 0.81). Controls
found: none for X, S and R_mf in any cell; at most 1 of 30 for P and PRR. PRR's column is the same as in run 001.

**Ramps, 104-week look-back** (the eight quarters the handoff proposes for V004). Plants found, of 30, with the
controls found in brackets where any were. The gate reads only the 26-week look-back. The last column says whether
the cell would pass at 104 weeks.

| Rate a week | Years | X | S | R_mf | P | PRR | Would pass |
|---|---|---|---|---|---|---|---|
| 0.1 | 2 | 0 | 0 | 0 | 5 (3) | 11 (2) | no |
| 0.1 | 4 | 0 | 0 | 0 | 11 (4) | 15 (3) | no |
| 0.5 | 2 | 1 | 1 | 2 | 8 | 18 (2) | no |
| 0.5 | 4 | 2 | 2 | 5 | 18 (1) | 30 (1) | yes, PRR |
| 2.0 | 2 | 21 | 21 | 27 | 11 (2) | 1 (2) | yes, R_mf |
| 2.0 | 4 | 19 | 19 | 27 | 27 | 30 (1) | yes, PRR |

In run 001, PRR found 9, 30 and 30 plants in the three 2-year rows, and those at 0.5 and 2.0 a week would have
passed. Run 002 finds 11, 18 and 1 (item 4).

**Sextuplings, 26-week look-back.** Plants found, of 30. Sextuplings are reported, not gated: the report prints "not
gated" for them.

| Years | X | S | R_mf | P | PRR |
|---|---|---|---|---|---|
| 2 | 1 | 1 | 3 | 18 (controls 3) | 18 |
| 4 | 7 | 7 | 7 | 18 | 16 |
| Both | 8 of 60 | 8 of 60 | 10 of 60 | **36 of 60** | 34 of 60 |

With a 104-week look-back, P found 39 of 60 (16 of 60 controls) and PRR 35 of 60 (3 of 60 controls).

## What it says

1. **The replays could not have passed.** At their setting, 0.1 a week over two years with a 26-week look-back, X, S
   and R_mf found none of 30 plants. This agrees with the handoff's 0 of 270, from another simulation. The best
   channel here, PRR, found 6 of 30.
2. **The pooled channel misses the 80% target on sextuplings.** It found 36 of 60 (0.60, 95% interval 0.47 to 0.71)
   in run 001, and the same 36 of 60 in run 002; the fix did not touch P. On the same plants the state-split channels
   found 8 of 60 (X and S) and 10 of 60 (R_mf). Pooling helps, by about four times, but not to 80%. The handoff's "90%
   pooled" came from a simulation whose settings are not in the repository; this check does not reproduce it. Nothing
   was tuned after either run.
3. **Why pooling falls short: the alert budget, and a flag that does not fit one site.** Both act together.
   - *The budget.* P raises 5 alerts in every evaluated week, the whole budget (425 in each 2-year world, 945 in each
     4-year world). The state-split channels raise 7 to 28 in a world. P had a median of 68 to 72 candidates a week,
     by world, for those 5 places. Of the 24 sextuplings P missed at 26 weeks, 20 were candidates in the look-back
     but never among a week's top 5 there. The other 4 were candidates only outside it. Every one was a candidate at
     some point, and none was held back by the cooldown.
   - *The flag.* The ranker takes 1.5 off the logit of a candidate flagged `high_base_rate`. The flag is set when, at
     more than half the eligible sites (`base_rate_site_fraction`, 0.5), another vehicle with the same failure
     exceeds its baseline that week. It is meant to mark a failure that is common everywhere. P has one site, so the
     flag is set whenever any other vehicle with that failure exceeds, which on a common failure is nearly always.
     Over the six worlds, 12,515 of 13,578 candidate keys (92%) carried it in their snapshot, and 2,820 of those
     alerted. Of the 1,063 without it, 1,061 alerted: they were 27% of the keys that alerted. All 60 sextuplings
     carried it, with scores 0.014 to 0.192. So with one site, every candidate on a common failure takes the
     penalty, and keys on failures where nothing else exceeded go first for the 5 places.
   - P runs with the cross-site minimums at 1 because it has one site. Treating `base_rate_site_fraction` the same way
     for one site would change the instrument. That is a decision for the chief scientist, not a tuning made here.
     Nothing was changed.

   The numbers come from `tools/market/power_diagnose.py`, run for each world (years 2 and 4, seeds 1 to 3; 12 to 35
   seconds a world). It rebuilds the world as the check does and runs P and PRR through the audit's own code. Its
   found counts equal run 002's for P's sextuplings and PRR's ramps.

   ```
   python tools/market/power_diagnose.py --pack docs/collective/replay/vehicles/pack \
       --background docs/collective/power/vehicle-background.json --years 2 --seed 1
   ```
4. **PRR warns long before the outcome, but often loses a signal already there when the export begins.** A ramp
   starts 104 weeks before its outcome. In a 4-year world that is well inside the export. In a 2-year world it is cut
   at the export's first week, so the signal is already there when the export begins. PRR alerts once a key's share
   is disproportionate. It then stays cooling while the key keeps signalling, so a steady ramp mostly alerts once,
   long before the outcome. The observed leads, from `power_diagnose.py` over the six worlds, run from the first alert
   in the 104-week look-back to the outcome's opening:

   | Rate a week | Years | Found, of 30 | Lead, weeks | Median lead, weeks |
   |---|---|---|---|---|
   | 0.1 | 2 | 11 | 9.1 to 75.1 | 40.1 |
   | 0.1 | 4 | 15 | 2.1 to 101.1 | 40.1 |
   | 0.5 | 2 | 18 | 29.1 to 67.1 | 54.6 |
   | 0.5 | 4 | 30 | 30.1 to 89.1 | 65.1 |
   | 2.0 | 2 | 1 | 65.1 | 65.1 |
   | 2.0 | 4 | 30 | 72.1 to 94.1 | 82.6 |

   Every observed lead at 2.0 a week is longer than 26 weeks, so the 26-week look-back finds none of those ramps. In the
   4-year worlds, 29 of the 30 ramps at 2.0 a week alerted exactly once; the other also alerted before its ramp
   began. At 0.1 a week some finds may be background: PRR also found 2 and 3 of 30 controls.

   Run 001's 30 of 30 at 2.0 a week over two years came from alerts in the first evaluated weeks on signals already
   there when the export began. Its walk (commit `a4497dd`), re-run on the three 2-year worlds, put all 30 of those
   alerts at week index 19 to 23 of 104; index 19 is the first evaluated week. Walked from the first week, 29 of the
   30 ramps alert at index 1 to 18, are dropped, and stay cooling. The one left alerted at index 21, 65.1 weeks
   before its outcome. At 0.5 a week over two years, 16 of 30 ramps alerted before the first evaluated week. The 12
   that PRR missed are all among them, and its finds fell from 30 to 18.
5. **V004 as planned would not pass.** With DIAG-E001's 0.1 a week, four years of history and an eight-quarter
   look-back, the best channel found 15 of 30 (PRR), with 3 of 30 controls. A replay at that signal size is not
   pre-registered under the rule. At 0.5 a week with that look-back, PRR finds 30 of 30 with four years of history,
   but 18 of 30 with two.

## What it does not say

- It does not say real complaints hold a signal. It says what the instrument could see if they did.
- The background is shaped by a few recorded means and several assumptions. Real vehicles differ in their failure mix,
  which would give PRR false alarms this background does not have. Real states are not 1 / rank, and real volumes
  change over time.
- The plants and detectors share an author. The ramp (linear from 0, two years) simplifies DIAG-E001's mean curve,
  which rises about fourfold over quarters -8 to -1; no single event is modelled.
- Thirty plants per cell give wide intervals: 20 of 30 is 0.49 to 0.81.
- A pooled channel with its own budget or with the base-rate flag set for one site, or the state-split channels with
  settings for 60 sites (plan item 1.5), are different instruments. They were not tested here.

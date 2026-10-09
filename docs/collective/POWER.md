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
  before the outcome opens. In run 001 a ramp at 0.1 a week added 5.0 to 5.6 records on average, and one at 2.0
  added 102.7 to 104.1;
- a **sextupling** runs one vehicle and failure at six times its own rate for the 13 weeks before the outcome.

Each plant has a **control**: an unplanted vehicle and failure from the same band, with an outcome opened the same day.
Outcomes open in the last 26 weeks of the export. Each world goes through `pilot.audit` unchanged, with all five
channels: X, S and model-free R split by state, and the two comparators, P (pooled national) and PRR
(disproportionality).

**Power** is the share of plants with an alert on their own vehicle and failure, available in the look-back before the
outcome opens. The **control share** is the same share over the controls. **The gate**, per ramp rate and history: the
best channel must find at least 80% of plants, counting only channels that find at most 20% of controls.

## Run 001: the vehicle-like background

Command, run in this sandbox. The grid ran on the uncommitted work that became commits `1c069f2` and `f837670`. Those
commits add two edits that do not change results (a tidier PRR loop and a clearer background error). The 2-year,
seed-1 world, re-run on the committed code, came out identical to the grid's record.

```
python -m mycelic.collective.pilot.power run --pack docs/collective/replay/vehicles/pack \
    --background docs/collective/power/vehicle-background.json --out <dir> --lookback-weeks 26,104 --workers 3
```

Six worlds (2 and 4 years, seeds 1 to 3), 30 plants and 30 controls per cell, 576 seconds with 3 workers. Each 2-year
world held 28,721 to 28,939 records; each 4-year world 55,969 to 56,327. The full output is `power/grid-001.json`
(sha256 `8902fbda...5c29cf`) and its report `power/grid-001.md`. The command exited 1: the gate failed.

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
found: none for X, S and R_mf in any cell; at most 1 of 30 for P and PRR.

**Ramps, 104-week look-back** (the eight quarters the handoff proposes for V004). Plants found, of 30, with the
controls found in brackets where any were:

| Rate a week | Years | X | S | R_mf | P | PRR | Passes |
|---|---|---|---|---|---|---|---|
| 0.1 | 2 | 0 | 0 | 0 | 5 (3) | 9 (2) | no |
| 0.1 | 4 | 0 | 0 | 0 | 11 (4) | 15 (3) | no |
| 0.5 | 2 | 1 | 1 | 2 | 8 | 30 (2) | yes, PRR |
| 0.5 | 4 | 2 | 2 | 5 | 18 (1) | 30 (1) | yes, PRR |
| 2.0 | 2 | 21 | 21 | 27 | 11 (2) | 30 | yes, PRR |
| 2.0 | 4 | 19 | 19 | 27 | 27 | 30 (1) | yes, PRR |

**Sextuplings, 26-week look-back.** Plants found, of 30:

| Years | X | S | R_mf | P | PRR |
|---|---|---|---|---|---|
| 2 | 1 | 1 | 3 | 18 (controls 3) | 18 |
| 4 | 7 | 7 | 7 | 18 | 16 |
| Both | 8 of 60 | 8 of 60 | 10 of 60 | **36 of 60** | 34 of 60 |

## What it says

1. **The replays could not have passed.** At their setting, 0.1 a week over two years with a 26-week look-back, X, S
   and R_mf found none of 30 plants. This agrees with the handoff's 0 of 270, from another simulation. The best channel here, PRR, found 6 of 30.
2. **The pooled channel does not reach the 80% target on sextuplings.** It found 36 of 60 (0.60, 95% interval 0.47
   to 0.71). On the same plants the state-split channels found 8 of 60 (X and S) and 10 of 60 (R_mf). Pooling helps,
   by about four times, but not to 80%. The handoff's "90% pooled" came from a simulation whose settings are not in
   the repository; this check does not reproduce it. Nothing was tuned after the run.
3. **Why pooling falls short: the alert budget.** P and PRR raise 5 alerts in every evaluated week, the whole budget
   (425 in each 2-year world, 945 in each 4-year world). The state-split channels raise 7 to 28 in a world. A
   diagnostic on the seed-1 worlds (rebuilt offline, not part of the check) found a median of about 70 pooled
   candidates a week. Of the sextuplings P missed there, 5 of 5 (2 years) and 3 of 4 (4 years) were candidates in the
   look-back that never ranked in the week's top 5; the other one was never a candidate.
4. **A short look-back hides an early warning.** PRR finds every ramp at 0.5 and 2.0 a week with a 104-week look-back,
   and none at 2.0 a week with a 26-week one. In the seed-1 worlds its one alert on each 2.0 ramp came 52 to 103 weeks
   before the outcome, and it did not alert again, as the cooldown implies for a key that keeps signalling.
5. **V004 as planned would not pass.** With DIAG-E001's 0.1 a week, four years of history and an eight-quarter
   look-back, the best channel found 15 of 30 (PRR), with 3 of 30 controls. A replay at that signal size is not
   pre-registered under the rule. At 0.5 a week, PRR passes at either history.

## What it does not say

- It does not say real complaints hold a signal. It says what the instrument could see if they did.
- The background is shaped by a few recorded means and several assumptions. Real vehicles differ in their failure mix,
  which would give PRR false alarms this background does not have. Real states are not 1 / rank, and real volumes
  change over time.
- The plants and detectors share an author. The ramp (linear from 0, two years) simplifies DIAG-E001's mean curve, which rises
  about fourfold over quarters -8 to -1; no single event is modelled.
- Thirty plants per cell give wide intervals: 20 of 30 is 0.49 to 0.81.
- A pooled channel with its own budget, or the state-split channels with settings for 60 sites (plan item 1.5), are
  different instruments. They were not tested here.

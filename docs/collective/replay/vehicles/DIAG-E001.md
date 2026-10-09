# Diagnostic E001: where is the signal in time?

**Exploratory and descriptive: no pass or fail, and not a test of the product.** It is written before it runs, to
check two design choices the five replays made without evidence:

- a **26-week look-back**: an alert counts only if it falls in the 26 weeks before a recall or investigation;
- an **export that starts on 2023-01-01**: the complaint file runs from 2020, so the detectors never saw what came
  before the window.

If the complaints about a recalled component rise one to three years before the recall, both choices hide the signal,
and the replays measured the wrong thing. If they rise only around or after the event, the choices did not matter.

**What it computes** (`tools/market/nhtsa_event_study.py`; run by the vehicle-probe workflow):
- For the six makes of V001 to V003, it reads every recall campaign on a vehicle, and every preliminary evaluation or
  defect petition on a vehicle, dated 2018-01-01 to 2023-12-31.
- Around each event, it counts the vehicle's complaints received 2015-2024 per quarter, from 12 quarters before to 4
  after: all of them, and those filed under a component the event names.
- It reports how many events have any complaint on their vehicle at all, as a naming check between the files.
- It reports where each event's matching complaints from the 3 years before fall: inside the 26-week look-back,
  earlier, or before 2023.

Aggregates only; no complaint text is read or printed.

**How it is read:** it is descriptive and checks no hypothesis.
- If matching complaints sit mostly outside the look-back or before 2023, the replays' design is the first suspect.
- If the per-quarter curve rises well before the event, cross-site counting has something to find; the replays then
  need a longer look-back and history, set before any new replay runs.
- If the curve rises only after the event, the public complaint stream reacts to recalls more than it warns of them.

## Runs

1. **Run [37883069926](https://github.com/anovruzov/NeuralGraph/actions/runs/37883069926)** (commit `1423f76`). Its
   "before 2023" count pooled events from 2018 to 2022, for which every earlier complaint is before 2023 by definition,
   so that count says nothing about the replays. The other numbers stand. The script now counts "before 2023" only
   for events dated 2023 (`events_dated_from_2023`); run 2 repeats it with that fix.
2. **Run [37883179901](https://github.com/anovruzov/NeuralGraph/actions/runs/37883179901)** (commit `84810e6`,
   job 113667259044; aggregates transcribed from the log, artifact `vehicle-probe`). 332,225 complaints on 3,420
   vehicles of the six makes, 2015-2024.

   **Where each event's matching complaints from the 3 years before fall:**

   | Events | Events | With matching complaints before | Matching complaints, 3 years before | In the 26-week look-back | Before 2023 (unseen by the replays) |
   |---|---|---|---|---|---|
   | Recalls, all 2018-2023 | 3,213 | 1,631 | 49,421 | 7,525 (15%) | not meaningful (see run 1) |
   | Recalls dated 2023 | 589 | 261 | 3,611 | 1,022 (28%) | 2,445 (68%); mostly before 2023 for 164 of 261 events |
   | Investigations, all 2018-2023 | 457 | 262 | 4,296 | 1,412 (33%) | not meaningful |
   | Investigations dated 2023 | 242 | 120 | 1,621 | 457 (28%) | 1,309 (81%); mostly before 2023 for 102 of 120 events |

   Of the recall events, 2,950 of 3,213 have at least one complaint on their vehicle, and 411 of 457 investigation
   events do: the naming of vehicles mostly agrees between the files.

   **Mean matching complaints per event, by quarter relative to the event** (quarter 0 starts on the event's date):

   | Quarter | -12 | -10 | -8 | -6 | -4 | -3 | -2 | -1 | 0 | 1 | 2 | 3 |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|
   | Recalls | 2.27 | 1.38 | 1.07 | 1.22 | 0.97 | 1.07 | 1.04 | 1.30 | 2.00 | 1.78 | 1.54 | 1.35 |
   | Investigations | 0.24 | 0.38 | 0.43 | 0.70 | 1.18 | 1.12 | 1.34 | 1.75 | 4.90 | 3.22 | 3.19 | 3.81 |

   The matching share of the vehicle's complaints moves the same way. For recalls it falls from 0.36 at quarter -12 to
   0.15 at quarter -1, then rises to 0.19 at quarter 0. For investigations it rises from 0.05 at quarter -12 to 0.20 at
   quarter -1 and 0.34 at quarter 0.

   **What it says, descriptively:**
   - Before an investigation opens, complaints about the investigated component rise steadily for about two years
     (quarters -8 to -1, about fourfold).
   - Before a recall there is no such rise in the last two years; matching complaints were highest three years
     before. The recalls in this window often follow earlier investigations or earlier complaint waves.
   - In both cases most of the build-up lies outside a 26-week look-back.
   - For events in 2023 most of it lies before 2023, where the replays exported nothing.

   So the replays' window and look-back hid most of the pre-event complaints. Whether the detectors would flag the
   rise if they could see it is still untested.

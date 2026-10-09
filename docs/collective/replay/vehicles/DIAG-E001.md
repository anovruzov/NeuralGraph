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

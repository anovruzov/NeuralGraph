# B1b attempt 3 (the last): plan, committed before any alert is computed on its digest

Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.

Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.

- **Scenario bytes:** `scenario_codes_miss.json` here, sha256
  `5578b5500d2edc4001d9a635b10a61df7c9e9b81701948d122cbc0146f754af6`, scenario digest
  `5578b5500d2edc4001d9a635b10a61df`; byte-identical to `demo/collective/scenario_codes_miss.json` in the commit that
  adds this plan.
- **Why another attempt is allowed:** attempt 2's own scorecard (`../attempt-2/scorecard.json`) fails an attempt-fatal
  check (`/codes_miss/gate_status` is `stale`) and has `/codes_miss/holds` false. This is attempt 3 of 3: whatever it
  shows is final, and it becomes the committed scenario (PREREG section 6).

## What changes, and why

**Against the pre-registered cast (attempt 1's bytes), one adjustable field: the hero's start week, 30 becomes 32**;
the shift follows by R8 (start week 24, running 16 weeks to the end of the world). The hero lot returns to the
pre-registered `L10002`: attempt 2's lot change did not hide the case from R (R flagged the hero's product
`product:SD-12:malfunction_unspecified` in the hero's first week instead of the lot), so it is dropped. Everything
else is the PREREG section 3 cast
(`tests/mycelic/test_collective_codes_miss.py::ParseTests::test_only_adjustable_fields_differ_from_the_preregistered_cast`).

**Why the start week.** Attempt 2's gate went stale: "the newest confirming week 2024-W36 ended more than 42 days
before 2024-10-28" (`../attempt-2/scorecard.json`, `/hero/pushdown/gate/reasons/0`). A hero that starts in week 30
runs to 2024-W36, which ends 50 days before the check's as-of date, so the gate is stale on the hero's own records;
attempt 1 passed only because background records at the confirming plants matched its lot and failure mode in later
weeks. That is a flaw of the cast, not of the case. The rule chosen before looking at any alert: **the earliest hero
start week whose last week ends within the gate's 42 days of the as-of date.** By date arithmetic on the built world
(as-of 2024-10-28, from the latest record date plus 7 days and the 14-day close lag): start 30 ends 2024-09-08, 50
days before; 31 ends 2024-09-15, 43 days; **32 ends 2024-09-22, 36 days**. So 32.

**What this does not address.** Across the 28 worlds the first two attempts logged, R or S flags one of the hero's
structured keys within about two weeks of X in most of them, and about half the robustness seeds hold. Nothing here
is chosen to change that; this attempt may well not hold, and if it does not, the pages say so.

**How it will be recorded:** `python demo/collective/collective_demo.py --record DIR --run-id collective-tarnwick-b1b
--scenario demo/collective/scenario_codes_miss.json`, with the deterministic stand-in, on the code of this commit;
its six run files and a README read from its scorecard are committed here afterwards, whatever they show.

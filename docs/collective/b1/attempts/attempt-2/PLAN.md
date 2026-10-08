# B1b attempt 2: plan, committed before any alert is computed on its digest

Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.

Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.

- **Scenario bytes:** `scenario_codes_miss.json` here, sha256
  `5a92c52827c79f43174fbd268402db3d470052791f97325b86d69d9ed1d3a31f`, scenario digest
  `5a92c52827c79f43174fbd268402db3d`. It is byte-identical to `demo/collective/scenario_codes_miss.json` in the commit
  that adds this plan.
- **Why another attempt is allowed:** attempt 1's own scorecard (`../attempt-1/scorecard.json`) passes the
  attempt-fatal checks (`/codes_miss/main/x_caught` true, `/codes_miss/gate_status` `supported`) and has
  `/codes_miss/holds` false (PREREG section 6). This is attempt 2 of at most 3.

## What changes, and why

**One adjustable field: the hero lot**, `L10002` becomes `L20045`. What follows from it and nothing else: the hero's
key, slot and structured lot; its structured product becomes `SD-12`, the lot's parent in the generator's links (R4);
the sibling holds the same lot and product (a sibling holds the hero's entity). The sites, languages, rate, start
week, templates, seeds, weeks, org, shift, decoys, rule and grid are unchanged
(`tests/mycelic/test_collective_codes_miss.py::ParseTests::test_only_adjustable_fields_differ_from_the_preregistered_cast`).

**Why this change.** In attempt 1, R (model-free) and S both flagged `lot:L10002:malfunction_unspecified` in the
hero's first week (`/codes_miss/main/channels/R_mf/attributed/0` and `/codes_miss/main/channels/S/attributed/0`,
week `2024-W31`, ranks 1 and 2), a week before X (`/codes_miss/main/x_week`, `2024-W32`). R reads record-level counts
of the allowed fields, and the hero's lot field is filled in 60% of the hero records, so a failure on a low-volume lot
is a visible rise of that lot under the generic code. The realistic hard case for central trending is a failure on a
high-volume lot. The rule chosen before looking at any alert: **among the lots R3 allows at all three hero plants
(`L10001`, `L10002`, `L20045`), take the one with the most background records at those plants.** Measured on the
world without the hero (building only, no detection): `L10001` 10 records, `L10002` 12, `L20045` 42 over the 40
weeks; 1, 6 and 15 in weeks 22 to 35. So `L20045`.

**What this does not change.** The disclosure stands: the authors chose this lot knowing why attempt 1 failed. If
attempt 2 holds, it shows the mechanism on a constructed world where the case sits on a high-volume lot; it says
nothing about how often such cases occur. If it does not hold, the pages say so, and at most one attempt remains.

**How it will be recorded:** `python demo/collective/collective_demo.py --record DIR --run-id collective-tarnwick-b1b
--scenario demo/collective/scenario_codes_miss.json`, with the deterministic stand-in, on the code of this commit;
its six run files and a README read from its scorecard are committed here afterwards, whatever they show.

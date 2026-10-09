# Public replay V001: vehicle complaints, a second field on real data

The device replays (001 and 002) tested one manufacturer with eight recalls. This replay tests the same pipeline in a
different field, US vehicle safety complaints to NHTSA, where public recalls are far more numerous. It runs through
the pilot audit (`mycelic.collective.pilot.audit`, `docs/collective/PILOT.md`), the tool a design partner would run on
its own export. The rule below is committed before any recall's content is read. The probe that informs it read only
complaint volumes and the files' published field lists, plus the recall file's size and row count
(`nhtsa-probe.json` here; runs [37868811969](https://github.com/anovruzov/NeuralGraph/actions/runs/37868811969) and
[37869126659](https://github.com/anovruzov/NeuralGraph/actions/runs/37869126659)).

## The declaration

The rule was written by the AI system that wrote this repository's code. Its training data includes public news of
vehicle recalls, including those of the make the rule picks, which is among the most recalled in recent years. It
cannot rule out that this shaped the rule, so every number from this replay carries **`saw_recall_outcomes: yes`**.
The make is chosen by complaint volume before the window, not by recalls.

## The rule

1. **Data.** NHTSA's public flat files: complaints received 2020-2024 (`COMPLAINTS_RECEIVED_2020-2024.zip`) and recalls
   since 2010 (`FLAT_RCL_POST_2010.zip`), as published on the day of the run.
2. **Make.** The make with most complaints added in 2019 (before the window): **FORD** (18,637; the probe).
3. **Window.** Complaints received by NHTSA (`LDATE`) from 2023-01-01 to 2024-12-31. Recalls whose Part 573 report
   was received (`RCDATE`) in the same window are the outcomes.
4. **Records and sites.** One record per complaint (`ODINO`) of product type vehicle, with a two-letter consumer state
   and a known model year. **Each state is a site**, standing for the separate plants, regions or subsidiaries of a
   company that cannot pool their records. The vehicle is make-model-model year (`vehicle_pack.vehicle_id`). Codes are
   the complaint's top-level component categories, and the narrative is NHTSA's complaint description. Nothing else
   is exported: no VIN, city, dealer, incident state or operator's name.
5. **The pack** (`pack/`, built by `tools/market/vehicle_pack.py` from the probe):
   - the predicates are NHTSA's top-level component categories with at least 1,000 complaints in 2015-2024, each
     with its own name's phrases as its lexicon (a shared phrase goes to the larger category; VISIBILITY, left with
     none, folds into unknown or other): 26 categories and the generic one;
   - everything else is the device pack's structure: detectors, egress limits (k = 3), negation and extraction
     settings.
6. **Outcomes.** One per recall campaign and vehicle (`CAMPNO/vehicle`), opened on its report date. Its failure is
   the recall's component category when the campaign names exactly one of the pack's categories for that vehicle;
   otherwise the outcome matches any failure on that vehicle.
7. **The audit.** `pilot.audit run` with look-back 26 weeks, post-recall 26 weeks and tie salt `pilot`, exactly as a
   partner would run it. X reads codes and narratives lexically (no model), S reads the codes only, and model-free R
   reads record-level codes.
8. **The first run is the result.** A run that fails before the audit for an infrastructure reason (a download, a
   runner) may run again unchanged; any change to a setting is a new choice file.

## How it is read

- A found count means something only as far as it beats its own chance column: the circular-shift null over the
  evaluated weeks. A recall can be found only by an alert on its own vehicle.
- Outcomes of one campaign share their timing, and the shift null moves all alerts together, so it accounts for that.
  The report also lists how many campaigns lie behind the outcomes.
- **What it is not:**
  - states are not a company's sites;
  - public complaints are not a company's own complaint file;
  - the lexical reader is not a model;
  - a recall is not the moment a manufacturer first knew.

  It is a second, larger public test of whether cross-site counting of complaints flags a problem before it is acted
  on, run by the same tool a partner would run.

## Runs

1. **Run 001** ([37869797640](https://github.com/anovruzov/NeuralGraph/actions/runs/37869797640), commit `91b107e`,
   job 113625006888; audit sha256 `e400c485eb92c1e848eb3619794cd49cd92161926cf8c839bac265e90029d851`, matching the one
   the job printed). Read in full, and **its result was seen before run 002**.
   - **Export:** 27,397 FORD complaints of 72,114 FORD rows (35,525 outside the window, 8 not vehicles, one with no
     state, one with no model year); 60 sites (the 50 states, DC, six territories
     and freely associated states, two military mail codes and `CD`, a code the file does not explain); none rejected;
     every record has a narrative and resolves to a vehicle.
   - **A bug: no codes reached the audit.** The exporter named its list column `components[]` and the audit's CSV
     writer adds `[]` to every list column, so the file's header was `components[][]`, which reads back as a field
     the mapping does not name. Codes coverage was **0 of 27,397**, so S and R_mf, which read codes only, had nothing
     to read, and X read the narratives alone. The offline tests checked the rows, not the header the audit reads; they
     now check the header, codes coverage and that a planted burst reaches all three channels.
   - **Outcomes:** 117 campaigns, 373 campaign-vehicle outcomes, 298 in scope (opened after the first evaluated
     week, so their look-back has evaluated weeks); 87 evaluated weeks, 2023-W19 to 2025-W01.
   - **Result, as it ran:** X raised 128 alerts and found **0 of 298** outcomes before their recall, against 5.75 by
     chance (p 1.0); 112 of its alerts matched no outcome. S and R_mf raised no alerts. The review list has 67
     patterns.
2. **Run 002** (`run-002.json`; [37870255727](https://github.com/anovruzov/NeuralGraph/actions/runs/37870255727),
   commit `e56feba`, job 113626463564; audit sha256
   `1cf00bb969afbea58d717dfe12815b569748f254bf021f4dac42f69ea7481acb`, matching the one the job printed): the same
   rule, make, window and settings, with the exporter fixed. It is not a fresh test: the AI agent saw run 001's
   result, in which text-only X found nothing. The fix changes only what reaches the audit (the codes column); no
   setting, threshold or pack file changed. Both runs stand.
   - **Export:** the same 27,397 complaints and 60 sites; codes, narrative and vehicle on every record.
   - **Outcomes:** the same 298 in scope, from 91 campaigns; 87 evaluated weeks.
   - **Result:**

     | Channel | Alerts | Found before the recall, of 298 | Median days earlier | Expected by chance | p | Alerts matching no outcome |
     |---|---|---|---|---|---|---|
     | X | 262 | 5 | 47 | 10.95 | 0.84 | 227 |
     | S | 221 | 5 | 47 | 10.92 | 0.84 | 188 |
     | R_mf | 270 | 5 | 47 | 12.34 | 0.89 | 233 |

     The three channels find the same five outcomes, of five campaigns, 12 to 152 days before the report. **Each finds
     fewer than chance would**: the same alerts moved to random weeks find 11 to 12. Of the review list's 158
     patterns, 115 come from all three channels and 20 from X alone.

## What the two runs say

On two years of one make's public complaints, with every state as a site and the make's own recalls as the outcomes,
cross-site counting of complaint codes and narratives **gave no early warning beyond chance**. The narratives added
little over the codes: X and S find the same outcomes. The device replay with readable identifiers (002,
`docs/lab/RESULTS.md` run 4) gave the same answer on eight recalls; this one gives it on 298 outcomes. What it does
not test: a company's own complaint, warranty and service records, its real sites, a model reading the narratives,
and outcomes dated when the company first investigated rather than when it reported a recall.

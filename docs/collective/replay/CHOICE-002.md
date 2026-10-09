# Public replay 002: replay 001's choices, with identifier formats taken from the manufacturer's earlier reports

Replay 001 ran end to end and no channel raised an alert, because the `device_quality` pack's id formats (written for
its synthetic generator) resolve few of Becton Dickinson's real lot and product identifiers: the replay itself warned
`low_resolution` ([CHOICE-001](CHOICE-001.md), run 2). Replay 002 changes **only the pack**, by the rule below, which
is committed before the probe that feeds it has run. Every other setting is replay 001's.

## What is known before this file, and the declaration

- **The declaration stays `saw_recall_outcomes: "yes"`**, for CHOICE-001's reason (the agent's training data may cover
  this manufacturer's recalls) and because replay 001 has already been scored on the same data.
- **What replay 001 showed**: the recall dataset holds 22 records for the four product codes, 8 of them filed under
  the listed Becton Dickinson spellings, all in the window and evaluable; X, S and R (model-free) raised no alert at all.
  No recall's date, product code or cause was printed or read by the agent: the lab report gives counts only.
- **Who chooses.** The rule was written by the AI system that wrote this repository's code and the base pack
  (`same_author_as_code`), and it reuses the base pack's predicates, lexicon and detectors unchanged.

## What stays as in replay 001

Everything in `lab/requests/openfda-001.json`'s `openfda` block except `pack`: the ten manufacturer names, product
codes JKA, FOZ, FMI and MDB, the window 2023-01-01 to 2024-12-31, `max_records_per_code` 10,000, the site field
`device[].manufacturer_d_name`, the ten recalling-firm spellings, lookback and post-recall 26 weeks, minimum partition
coverage 0.5, tie salt `lab-replay`, 150 unit minutes and a 200-minute job. The request is
`lab/requests/openfda-002.json`.

## The rule (what changes: the pack)

1. **Probe** (`tools/market/openfda_id_shapes.py`, in the `market-count` workflow). For each of JKA, FOZ, FMI and MDB,
   among the device reports received **2021-01-01 to 2022-12-31** (the two years before the window) from manufacturer
   names containing "BECTON": the 100 most reported values of `device.lot_number`, `device.model_number`,
   `device.catalog_number` and `product_problems`, with report counts. Events only; nothing from the window, nothing
   from the recall dataset. Its output is copied byte for byte from the job log into `inputs-002.json` here.
2. **Pack** (`tools/market/replay_pack.py`, pure functions with offline tests): `lab/packs/device_quality_bd`, a copy
   of `device_quality` in which:
   - **Lot id format.** Of the lot values (summed over the four codes after trimming and upper-casing), values
     without a digit (`UNK`, `NA`) and values with a character other than A-Z, 0-9, `-` and `/` are dropped. Each
     remaining value's signature is its sequence of letter runs, digit runs and single separators. The signature with
     the largest report count wins (ties: fewer runs, then the signature's text). Its run lengths are the narrowest
     that cover its most reported values, taken in order of count, until they reach 90% of its count.
   - **Product field and format.** `device[].model_number`, unless `device[].catalog_number` has the larger report
     count of values with a digit; then the product format is the same rule applied to that field's values.
   - **Problem terms.** A probe term the base mapping does not already name maps to a predicate when its words contain,
     as consecutive whole words, the predicate's id (underscores read as spaces) or one of its English lexicon
     entries, and no other predicate's. It takes that predicate's first specific code (else its first code). Other
     terms stay unmapped; the six terms the base pack maps keep their codes.
   - **So that it loads**, and nothing else: the lot and product aliases are dropped (they name the base pack's
     fictional ids); the fictional lot and product ids in the generator and in the extraction fixtures are renamed
     into the new formats; the plant fixtures are left out. The replay reads neither generator nor fixtures.
   - Detectors, predicates, codes, rules, egress, questions and follow-ups are the base pack's, byte for byte.
3. If the rule gives no lot or product format, or the built pack does not load, replay 002 is not run and this file
   records that.
4. The pack is built from `inputs-002.json` by the tool in a later commit, and the request in the commit after that.

## How it is read

- **The first run is the result.** As in CHOICE-001: a run that fails before scoring for an infrastructure reason may
  be run again unchanged; any change to a setting is a new choice file.
- **The comparison is replay 001.** Same events, same recalls, same detectors and settings; what differs is how many
  of the manufacturer's identifiers and problem terms the pack can read. The lab report now carries the replay's
  coverage shares (resolved identifiers, mapped problem codes), each channel's alert count and its circular-shift
  null, so both runs' numbers can be set side by side.
- A channel's found recalls count only as far as they exceed its own `expected_found` under the circular-shift null
  (its `p_value` is the share of shifted timelines that do at least as well); the replay matches on product code
  alone, not on cause. Eight recalls are few: one found recall more than chance is not evidence of anything.
- The replay is model-free, public data and artificial partitioning: its own label says it is not a confidentiality
  demonstration. The declaration (`yes`) travels with every number from it.

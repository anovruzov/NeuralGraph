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
   from the recall dataset. The job log prints the output as one line of JSON with the sha256 of the indented file
   the tool writes; `inputs-002.json` here is that JSON written the same way, checked against the sha256.
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

## Runs

1. **Probe, first run** ([37865960984](https://github.com/anovruzov/NeuralGraph/actions/runs/37865960984), commit
   `373523a`): the probe and the rule's plan step succeeded, but the agent's log reader returns only a job log's last
   5,000 lines, and the probe's indented output began before them (the full log sits on a host this agent's network
   does not reach). **Nothing of it was read**: no value, count or format. The probe now prints one line and runs last,
   and is run again unchanged in its query.
2. **Probe, second run** ([37866279709](https://github.com/anovruzov/NeuralGraph/actions/runs/37866279709), commit
   `41089f0`, job 113613652857): read in full. `inputs-002.json` is its output; its sha256
   `5c429d682ab42b39ab82a5af9eb95c42d86161c2a98180926be9db9b0011eae3` matches the one the job printed. Reports received
   2021-2022 from names containing BECTON: JKA 6,446, FOZ 3,877, FMI 7,397, MDB 1,655.

## Applied (by `tools/market/replay_pack.py` from `inputs-002.json`)

- **Lot:** signature one digit run; format **6 to 7 digits**. Of the lot values' 8,613 reports, 4,917 carry a value
  with no digit (such as `UNK`) and 874 one with other characters; the format covers 2,764 of the 2,822 left.
- **Product:** `device[].catalog_number` (its values with a digit carry more reports than the model number's);
  format **6 to 8 digits**, covering all 15,105 reports with a digit of its 16,955.
- **Problem terms:** 16 terms added to the six the base pack maps (none ambiguous): Leak/Splash (leak); Fracture
  (crack); Blocked Connection, Complete Blockage and Partial Blockage (occlusion); Excess Flow or Over-Infusion and
  Insufficient Flow or Under Infusion (inaccurate reading or dose); Material Discolored and eight contamination terms
  (contamination). The mapped terms carry 7,154 of the 22,113 reports of the probe's top terms (32%); 135 terms stay
  unmapped, among them the generic ones.
- **The pack** `lab/packs/device_quality_bd` loads (version 0.1.0): config `12ca457bd546`, vocabulary
  `0baf809b1631`, detector `c9462f62aa90` (the base pack's, unchanged), fixtures `3e5cd71916f0`. The lot and product
  formats overlap at 6 and 7 digits; the replay resolves each structured field as its own type, so a value is read
  as the type of the field it sits in.
- **Plumbing check, synthetic, not a result:** on a generated event cache in this shape (catalog numbers, seven-digit
  lots, three listed names), prereg and signals ran with this pack, every record resolved to an entity, and all
  three channels flagged the burst planted in it. It shows the pack is wired, nothing about real data.

`lab/requests/openfda-002.json` holds replay 001's `openfda` block with only `pack` changed (and its `purpose`), and
is pushed in its own commit; that push starts the run.

### The result

[Lab run 37866601007](https://github.com/anovruzov/NeuralGraph/actions/runs/37866601007), commit `4a5a34f`,
2026-10-09 00:48 UTC; report from the `aggregate` job (113616573922), request sha `8f6599656974`.

- **Every step ran, with no warning.** Events fetched in full (FMI 9,039, FOZ 9,204, JKA 9,010, MDB 1,633, none
  truncated) and the same 22 recall records as replay 001, 8 of them in scope and evaluable; 87 weeks were evaluated.
- **The pack now reads the manufacturer's records:** of the reports under the listed names, 91.9% carry an identifier
  the pack resolves (replay 001 warned that fewer than half did), 34.4% a mapped problem code, 98.7% a lot number and
  15.7% a model number.

| Channel | Alerts | Recalls found, of 8 | Expected by chance | p | False alarms | Alerts after a recall |
|---|---|---|---|---|---|---|
| X (model-free) | 4 | 1, 31 days before initiation | 1.16 | 0.82 | 1 | 2 |
| S | 3 | 0 | 0.68 | 1.00 | 1 | 2 |
| R (model-free) | 5 | 0 | 0.71 | 1.00 | 3 | 2 |

- **What it shows: no early warning on this data.** With the identifiers readable, the detectors raised few alerts
  (3 to 5 in 87 weeks) and found no recall more often than chance: X's one find is what a randomly timed alert series
  gives (expected 1.16, p 0.82), and S and R found none. Each channel raised 2 alerts in the 26 weeks after a recall's
  initiation, which the scorer neither credits nor counts as false.
- **What it does not show.** One manufacturer, four product codes, eight recalls, two years; public reports, which
  carry far less than a manufacturer's own complaint files; model-free channels; matching on product code alone. It is
  not a test of a model reading the narratives, and not of data a customer holds. It is the first real-data test of
  detection in this repository, and it is negative. The declaration (`yes`) stands.

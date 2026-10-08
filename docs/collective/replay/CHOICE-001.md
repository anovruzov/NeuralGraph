# Public replay 001: the choices, made by a stated rule before any recall record is read

The public replay (`experiments/openfda_replay.py`, run by the lab's `openfda` unit) needs a manufacturer, its
product codes, a date window, the field that splits its reports into sites and the recalling firms' names, all fixed
before any recall outcome is looked at. Until now these were left to the founder. This file makes them **by a rule,
on the AI agent's side**, so that one real-data result exists; the founder's own choice remains open and would be a
separate replay with its own file.

## Who chooses, and the declaration

The rule below was written by the AI system that wrote this repository's code, and committed before the sizing
probe it depends on had run and before any recall record was fetched. The agent has read no recall record for this
replay. Its training data includes public reporting on medical device recalls, which may cover products of the
manufacturer chosen here, and it cannot rule out that this shaped the rule. The request therefore declares
**`saw_recall_outcomes: "yes"`**, and every result of this replay is read with that declaration.

## The rule

1. **Manufacturer group.** The group in `docs/strategy/MARKET.md` section 3.6 (run 37858992788) with the most product
   codes reported by at least two manufacturer names with at least 100 reports each and the largest name under 62%:
   **BECTON** (Becton Dickinson), 6 such codes.
2. **Manufacturer names** (`manufacturers`, matched exactly): the group's ten most reported names in 2024, as spelled
   in `docs/strategy/data/site-split.json`:
   `BECTON DICKINSON`; `BECTON, DICKINSON AND COMPANY (BD)`; `BECTON DICKINSON INFUSION THERAPY SYSTEMS INC.`;
   `BECTON, DICKINSON & CO. (BROKEN BOW)`; `BECTON, DICKINSON & CO., (BD)`; `BECTON DICKINSON MEDICAL SYSTEMS`;
   `BECTON DICKINSON & CO. (SPARKS)`; `BECTON, DICKINSON & CO. (SPARKS)`; `BECTON DICKINSON IND. CIRURGICAS LTDA`;
   `BECTON DICKNSON AND CO. - DUN LAOGHAIRE CO, IRELAND`.
3. **Candidate codes, in order**: the group's codes in that table by 2024 reports, most first: JKA, FOZ, FMI, FMF,
   FPA, MDB.
4. **Window and codes.** The replay fetches every manufacturer's reports for each code (the manufacturer filter
   applies after the fetch), and without an API key the lab allows 100 openFDA requests: at 5 codes, 10,000 reports
   per code. The window is whole calendar years of `date_received` ending 2024-12-31 and starting no earlier than
   2019-01-01. Take the longest such window in which at least 3 candidate codes have at most 10,000 reports from all
   manufacturers; in it, take the first (up to 5) candidate codes, in the order of step 3, that fit. If no window of
   one year or more holds 3 fitting codes, the replay is not run and this file records that.
   `max_records_per_code` is 10,000.
5. **Site field** (`partition_field`): `device[].manufacturer_d_name`, so each plant or entity name is a site. Names
   are matched exactly and nothing is merged (MARKET 3.6 notes that some names are spellings of one company).
6. **Recalling firms** (`recalling_firms`, matched exactly against the recall records' `recalling_firm`): the recall
   dataset's firm names that contain "BECTON" or begin with the word "BD", up to 10, in the order the probe returns
   them. The probe prints the names only: no count, date, product code or cause of any recall.
7. **Everything else** at the lab's defaults: pack `device_quality`, `manufacturer_field`
   `device[].manufacturer_d_name`, lookback 26 weeks, post-recall 26 weeks, minimum partition coverage 0.5, tie salt
   `lab-replay`, no labelling sheets.

Steps 4 and 6 are computed by `tools/market/openfda_replay_inputs.py` in the `market-count` workflow (events counts
and firm names only). The request file `lab/requests/openfda-001.json` is written from its output exactly as the rule
says, in a later commit.

## How it is read

- **The first run is the result.** A run that fails before scoring for an infrastructure reason (openFDA unreachable
  or rate-limited, a runner lost) may be run again unchanged; any change to a setting is a new choice file.
- The replay is model-free (the X, S and R channels with the lexical extractor), public data, artificial
  partitioning: its own label says it is not a confidentiality demonstration.
- A channel's found recalls count only as far as they exceed its own `expected_found` under the circular-shift null;
  the replay matches on product code alone, not on cause.
- The declaration above (`yes`) travels with every number from this replay.

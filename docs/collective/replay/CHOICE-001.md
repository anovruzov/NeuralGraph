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

## Applied (from the probe's output, after the rule was committed in `58140ed`)

The `market-count` workflow ran the probe in run
[37859888847](https://github.com/anovruzov/NeuralGraph/actions/runs/37859888847) (job 113592831380, 2026-10-08
23:33 UTC); its output, copied from the log, is `inputs-001.json` here (each code's yearly counts sum to the totals the
probe printed, and the rule's window recomputes from them).

- **Window:** 2023-01-01 to 2024-12-31 (2 years). Over 2019–2024 or any window of 3 or more years, fewer than 3 codes
  fit the cap.
- **Codes:** JKA 9,010, FOZ 9,204, FMI 9,039 and MDB 1,633 reports from all manufacturers in the window. FMF (10,336)
  and FPA (58,125, of which 47,209 in 2024) do not fit.
- **Recalling firms:** the ten names the probe returned, in its order: `Becton Dickinson & Company`;
  `Becton Dickinson & Co.`; `Becton Dickinson Medical Systems`; `Becton, Dickinson and Company, BD Biosciences`;
  `Becton Dickinson and Company`; `Becton Dickinson Infusion Therapy`; `Becton Dickinson Infusion Therapy Systems,
  Inc.`; `Becton, Dickinson and Company, BD Bio Sciences`; `Becton Dickinson Infusion Therapy Systems Inc.`;
  `Becton, Dickinson and Company`. A recall filed under another spelling is out of scope and is counted only as
  `other_firm`.
- **Run sizing** (not an analysis setting): the unit gets 150 minutes and the job 200.

`lab/requests/openfda-001.json` holds exactly these settings and is pushed in its own commit; that push starts the run.

## Runs

1. **Lab run [37861226513](https://github.com/anovruzov/NeuralGraph/actions/runs/37861226513)**, 2026-10-08 23:46
   UTC, commit `add9302`: **failed before any data**. The first step (`fetch-events`) exited 2 in 0.4 s ("the openFDA
   fetch was refused"). The cause, found by `tools/market/openfda_connector_check.py` in run
   [37861797336](https://github.com/anovruzov/NeuralGraph/actions/runs/37861797336): openFDA answers a page of 1000
   records without an API key with HTTP 403 `API_KEY_MISSING` ("No api_key was supplied"), while pages of 5 and 100
   are served (HTTP 200, 9,010 JKA reports in the window, as the probe counted). The lab asked for pages of 1000.
   No event page and no recall record was fetched, so nothing of the outcome was seen. The lab now asks for pages of
   100 without a key (a code fix, not a setting); the run is repeated with this request unchanged, as the rule above
   allows for a failure before scoring.
2. **Run 2**, after the fix (commit `4c05eb1`). The lab starts when a request file changes in a push and the manual
   trigger is not available to this agent, so only the request's `purpose` sentence changed; every setting in its
   `openfda` block is byte-identical to run 1's.

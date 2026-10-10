# Public sources for drafting test D001

D001 (`CHOICE-D001.md`) needs public records with free text and a filed category, in a field no pack covers. This
file records what the GitHub Actions runner could read when D001 was designed. It holds facts only. Every figure
below is from the probe record, except the field count, which was counted from the probe record's field list with
Python's `str.split`.

## The probe record

- **Workflow:** `.github/workflows/onboard-probe.yml`. Schema only: status, size, field names and row counts.
- **Two runs, both on 2026-10-09:**
  - run [37982223500](https://github.com/anovruzov/NeuralGraph/actions/runs/37982223500) asked the CFPB complaint
    database (`tools/onboard/cfpb_probe.py`);
  - run [37982408547](https://github.com/anovruzov/NeuralGraph/actions/runs/37982408547) asked the sources in
    `tools/onboard/source_probe.py`.

  Which run asked which is read from the workflow file, whose comment says the first run asked CFPB, and from the
  run ids, which grow with time.
- **Nothing in the probe shows any record's values, any category distribution or any narrative.**
  `source_probe.py` reads headers, counts lines and lists field and link names; it prints no field value. CFPB
  refused every request, so `cfpb_probe.py` recorded nothing beyond the refusals.

## What each source answered

| Source | Answer from the runner | What it holds | In D001 |
|---|---|---|---|
| CFPB consumer complaints, API and bulk files | HTTP 403 to every request | not read | Not used. Never retried another way. |
| OSHA severe injury reports page | HTTP 403 | not read | Not used. |
| cpsc.gov/Data page | HTTP 403 | not read | Not used. |
| CPSC SaferProducts recall API | HTTP 200 | recalls only, no incident text | Not used: no narrative of an incident. |
| MSHA `Accidents.zip` | HTTP 200, 52,269,752 bytes | member `Accidents.txt`, pipe-delimited, 275,220 rows after the header, 57 fields (below) | **Used: the field no pack covers.** |
| MSHA `Mines.zip` | HTTP 200 | `Mines.txt`, pipe-delimited, 92,060 rows; its fields include `MINE_ID`, `CURRENT_CONTROLLER_ID`, `STATE`, `NO_EMPLOYEES` | Not used. |
| MSHA open-data page | lists `Violations.zip`, `OrdersIssued.zip`, `Inspections.zip`, `AssessedViolations.zip`, `ControllerOperatorHistory.zip` | not probed further | Not used. Possible outcomes for a later replay, not for D001. |
| openFDA device events (`api.fda.gov/device/event.json`) | HTTP 200 | 2,627,151 reports received in 2024; fields include `mdr_text`, `product_problems`, `device`, `date_received`, `manufacturer_name` | Not used (`CHOICE-D001.md`, section 2.3, says why). |
| NHTSA complaint flat files | already read by the vehicle replays (`tools/market/nhtsa_probe.py`, `docs/collective/replay/vehicles/nhtsa-probe.json`) | CMPL fields include `COMPDESC` (component), `STATE`, `DATEA`, `CDESCR` | **Used: the field where hand-built packs exist.** |

## MSHA accidents: the files

- **Data:** `https://arlweb.msha.gov/OpenGovernmentData/DataSets/Accidents.zip`, member `Accidents.txt`.
- **Field definitions:** `Accidents_Definition_File.txt` in the same directory
  (`https://arlweb.msha.gov/OpenGovernmentData/DataSets/Accidents_Definition_File.txt`). The probe record names it as
  the field definitions. D001's download step fetches it, and the run prints its lines for the declared columns.
- **The 57 fields of `Accidents.txt`, in order:** `MINE_ID`, `CONTROLLER_ID`, `CONTROLLER_NAME`, `OPERATOR_ID`,
  `OPERATOR_NAME`, `CONTRACTOR_ID`, `DOCUMENT_NO`, `SUBUNIT_CD`, `SUBUNIT`, `ACCIDENT_DT`, `CAL_YR`, `CAL_QTR`,
  `FISCAL_YR`, `FISCAL_QTR`, `ACCIDENT_TIME`, `DEGREE_INJURY_CD`, `DEGREE_INJURY`, `FIPS_STATE_CD`, `UG_LOCATION_CD`,
  `UG_LOCATION`, `UG_MINING_METHOD_CD`, `UG_MINING_METHOD`, `MINING_EQUIP_CD`, `MINING_EQUIP`, `EQUIP_MFR_CD`,
  `EQUIP_MFR_NAME`, `EQUIP_MODEL_NO`, `SHIFT_BEGIN_TIME`, `CLASSIFICATION_CD`, `CLASSIFICATION`, `ACCIDENT_TYPE_CD`,
  `ACCIDENT_TYPE`, `NO_INJURIES`, `TOT_EXPER`, `MINE_EXPER`, `JOB_EXPER`, `OCCUPATION_CD`, `OCCUPATION`,
  `ACTIVITY_CD`, `ACTIVITY`, `INJURY_SOURCE_CD`, `INJURY_SOURCE`, `NATURE_INJURY_CD`, `NATURE_INJURY`,
  `INJ_BODY_PART_CD`, `INJ_BODY_PART`, `SCHEDULE_CHARGE`, `DAYS_RESTRICT`, `DAYS_LOST`, `TRANS_TERM`,
  `RETURN_TO_WORK_DT`, `IMMED_NOTIFY_CD`, `IMMED_NOTIFY`, `INVEST_BEGIN_DT`, `NARRATIVE`, `CLOSED_DOC_NO`,
  `COAL_METAL_IND`.

## NHTSA complaints: the file

- `https://static.nhtsa.gov/odi/ffdd/cmpl/COMPLAINTS_RECEIVED_2020-2024.zip`, the file the vehicle replays and
  reader tests R001 and R002 read (`tools/market/nhtsa_export.py`, `COMPLAINTS_URL`).

## What the probe does not tell

- Any value of any field: no date format, no category label, no narrative, no site or company id.
- How many accident rows carry a narrative, or how many controllers, mines or years the file spans.
- The text encoding of `Accidents.txt`. The probe decoded it as Latin-1; that was the probe's choice, not a finding.

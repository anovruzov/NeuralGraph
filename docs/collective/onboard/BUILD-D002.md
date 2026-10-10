# Build note for D002: what changed in code

D002 (`CHOICE-D002.md`) is D001's rule with two changes: how a delimited line is split (rule 1.1) and when the run
starts (rule 9). This note says what changed in D001's code (`BUILD-D001.md`). It was built on branch `wf/d002` from
`7b41e1b`. The rule was committed first, alone (`d55872a`), and the code after it (`cdcb5de`).

No code was run on MSHA or NHTSA records. This machine is offline, and every test and the dry run used invented
records.

## What changed, file by file

| File | What changed |
|---|---|
| `mycelic/collective/onboard/exports.py` | Change (a). One function, `split_line(line, delim)`, reads one line with the `csv` module: that delimiter, the quote `"`, doubled quotes, no escape character, `strict` off, leading spaces kept. These are the module's defaults, written out in `QUOTING` so a later default cannot change them. Every delimited export goes through it line by line, the header too, comma files included. D001's whole-text comma reader is gone. When a line is longer than the module's field limit, the limit is raised for that line and put back after. |
| `tools/onboard/fetch_msha.py` | `split` reads the header and every line with `exports.split_line`, the same function. Each company file still holds the original lines, unchanged. |
| `tools/onboard/fetch_nhtsa.py` | No parsing change. It reads the complaint file through `tools/market/nhtsa_export.py`, the vehicle replays' code, unchanged, and writes each make's CSV one complaint per line. The drafter reads those files with the same rows as before (`NhtsaCsvReadTests`). |
| both download scripts | The User-Agent names no experiment: `mycelic-onboard (pack-drafting test on public data)`. |
| `mycelic/collective/onboard/score.py` | `experiment_id(settings)` returns the settings' experiment id. It must be a plain name (a letter, then at most 31 letters, digits, `_` or `-`), or it raises `ScoreError`. `arm.json` gains `experiment`. |
| `mycelic/collective/onboard/report.py` | The title, the verdict line, `report.json`'s `experiment`, the withheld report's title and the printed block markers all take the id from the settings. `withheld` and `block` take it as an argument. |
| `__main__.py`, `__init__.py` | Help texts and docstrings name no fixed experiment. |
| `docs/collective/onboard/D002-settings.json` | The bytes of `D001-settings.json`, with `"experiment": "D001"` replaced by `"experiment": "D002"`. sha256 `8bb9ba6ab9c8d4dca14ac86aa8df89f8cd1926b5355b9908ec3b71020c42c0e5`. |
| `.github/workflows/onboard-run.yml` | See below. |

**Unchanged on purpose:**

- the `kind` fields (`onboard_d001_settings`, `onboard_d001_arm`, `onboard_d001_report`). They name the file schemas
  D001 defined, which D002 keeps;
- the seeds `d001:boot`, `d001:sample` and `d001:permute`, as `CHOICE-D002.md` says;
- the language file's disclaimer, written into every drafted pack. It names `CHOICE-D001.md`, whose rule D002 keeps.
  Changing it would change the template that check 3b rebuilds;
- dates, roles, the drafter, the check, the scorer, `tools/market/`, `CHOICE-D001.md`, `D001-settings.json` and
  `run-001.json`.

## The workflow

The steps, in order:

1. the offline tests (the same command);
2. **the settings check:** the newest run file (`ls docs/collective/onboard/run-*.json | sort | tail -n 1`); its
   settings file's sha256 against the one it names; its experiment against its settings file's; the id must be a
   plain name. It writes `SETTINGS` and `EXPERIMENT` to the job's environment. A newest run file that fails a check
   fails the job before any download. The job never falls back to an older run file;
3. the two downloads (the same commands);
4. **the two splits**, `split both sources (a failure here is not a run)`. The step has no `if:`, so it runs only when
   the downloads passed;
5. **the run start:** `echo "=== $EXPERIMENT RUN-START ==="`, which prints `=== D002 RUN-START ===`;
6. score, audit, report, collect and upload, as before, each with `if: ${{ !cancelled() && steps.start.outcome ==
   'success' }}`.

The working directory is `work/` (it was `d001/`). The artifact is `onboard-${{ env.EXPERIMENT }}`, so
`onboard-D002`. Unchanged: the trigger, the pinned action commits, `contents: read`, no persisted credentials, Python
3.11, 180 minutes and the upload rules.

## Tests

- **Two tests failed at `7b41e1b`.** `AmendmentRecordTests` and `SecondAmendmentRecordTests` asserted that
  `CHOICE-D001.md`'s "Runs" read "None yet.", and `7b41e1b` recorded run 1 there. The workflow's offline-test step runs
  them, so a run on that tree would have stopped before its downloads. They now assert that "Runs" holds run 1 alone,
  after both amendments. A new test pins `CHOICE-D001.md`'s sha256 and `run-001.json`, so D001 stays frozen.
- **Changed to D002's rule, as strictly as before:** `WorkflowTests` (downloads, then splits, then the marker, then
  scoring; the marker reads `$EXPERIMENT`; no step before it has an `if:`; the new paths and artifact name);
  `WorkflowAmendmentTests` (paths under `work/`); `HonestFetchTests` (the new User-Agent); `OnboardGenericTests` (M1's
  column names from both settings files). `tests/onboard/test_onboard_fetch.py` now reads `D002-settings.json`.
- **New in `tests/mycelic/test_collective_onboard.py`, section 18:**
  - `QuotedFieldTests`: MSHA-style pipe and tab rows with every field quoted, a quoted mm/dd/yyyy date and a quoted
    narrative holding a pipe and a doubled quote; quoted and bare headers; quoted dates parsing as `M/D/YYYY`, where
    D001's split finds no format; an unbalanced quote that does not take in the next line; field-count rejects; a
    quoted delimiter; comma files reading as D001 read them; the one comma change (a quoted line break); a field over
    the module's limit; a quoted export drafting the same pack as a bare one.
  - `ExperimentIdTests`: D002 everywhere the report names the experiment, and never D001; any plain id; the withheld
    report; ids that are refused.
  - `D002SettingsTests`, `D002RecordTests` and `WorkflowD002Tests` (the settings check run under `bash`: the newest
    run file accepted; a mismatched experiment or changed settings refused with nothing set; the marker; the split
    step).
- **New in `tests/onboard/test_onboard_fetch.py`:** `QuotedMshaSplitTests` (the split uses the drafter's function;
  quoted dates parse and the company rule picks the same companies; D001's way of splitting finds no date format;
  company files hold the original quoted lines; the drafter reads them unquoted; only counts are printed; a quoted
  header reads the same) and `NhtsaCsvReadTests`.

Counts from `python -B -m pytest <file or directory> -q -p no:cacheprovider`, on this tree:

| Tests | Result |
|---|---|
| `tests/mycelic/test_collective_onboard.py` | 157 passed, 78 subtests passed |
| `tests/onboard/test_onboard_fetch.py` | 19 passed, 6 subtests passed |
| `tests/onboard` and `tests/market` | 106 passed, 39 subtests passed |
| `tests/mycelic/test_collective_guards.py` | 83 passed, 721 subtests passed |
| `tests/mycelic/test_collective_x3.py` (the hash pins, untouched) | 32 passed, 141 subtests passed |
| `tests/mycelic` | 1703 passed, 1 xfailed, 45573 subtests passed |
| the workflow's own command (`python -m unittest ...`, PyYAML installed here) | 181 tests, OK |

At `7b41e1b` the workflow's command ran 153 tests, and the two record tests above failed.

**The mutation run.** `run_mutants.py` (outside the repository) applied one mutant at a time to a copy of this tree and
ran the two onboard test files with `-x`. Its 19 mutants undo each piece of D002: `split_line` as a plain split, the
header split plainly, no doubled quotes, the field limit not raised or not put back, one reader across lines, the
MSHA split's lines or header split plainly, `D001` written into the title, `experiment`, the markers or the withheld
report, the id unchecked, the arm file without `experiment`, the marker before the splits, the run file's experiment
unchecked, the marker fixed to `D001`, a second value changed in the settings, and `CHOICE-D001.md` edited. The first
pass left one alive: the MSHA split reading its header with no quoting. `test_a_quoted_header_is_read_the_same` was
added, and a second pass killed all 19.

## The dry run

**The file.** `synth4.py` (outside the repository) is the D001 fix2 dry run's `synth3.py`, with the MSHA file written
as the date probe shows it:

- the header's names bare; every value in double quotes, a quote inside a value doubled; pipe-delimited;
- `ACCIDENT_DT` written mm/dd/yyyy;
- one narrative in four holds a pipe between two sentences, and one in four a word in double quotes: 67,011 and
  66,879 of the 275,220 rows.

Every value is invented. The NHTSA layout is `synth3.py`'s: 350,049 tab-delimited rows over seven makes, no quoting.
Read the probe's way (each line split at the pipe), the MSHA file gives what the probe gave: 275,220 rows, and every
`ACCIDENT_DT` value has the shape `"99/99/9999"`.

**D001's code fails on it as run 1 did.** `fetch_msha.py split` at `7b41e1b` stopped with `SplitError: no date format
parses enough of the date column` (exit 2).

**D002's code runs every step.** `run_pipeline2.py` (outside the repository) ran the steps as the workflow runs them
after the downloads, with `D002-settings.json`, on one machine with 4 cores:

| Step | Seconds | Seconds in D001 fix2's dry run (unquoted file, D-MON-YYYY dates) |
|---|---|---|
| split, MSHA layout | 7.3 | 4.9 |
| split, NHTSA layout | 12.4 | 12.4 |
| `=== D002 RUN-START ===` | printed after both splits | |
| score, MSHA arm (5 companies) | 141.4 | 132.8 |
| score, NHTSA arm (6 makes, two hand packs) | 279.5 | 273.1 |
| export of c1's test window | 1.6 | 1.4 |
| pilot audit of c1 | 16.4 | 17.1 |
| report, last guard and backstop | 14.8 | 13.4 |

- Every step exited 0. The verdict was pass, with all six criteria passed. The whole run took 473.4 seconds. The
  largest process used 738 MB.
- The MSHA split read 275,220 rows, rejected none and chose `M/D/YYYY`. It found 14 controllers, 13 qualifying, and
  used 5. Each MSHA company's draft read its file with no rejected row, and every date parsed as `M/D/YYYY`. Each
  NHTSA make's draft read 40,000 rows with no rejected row.
- None of the 1,798 printed labels and terms holds a double quote.
- `report.json`'s `experiment` is `D002`, both files' titles and markers read `D002`, and neither file holds `D001`.
- The last guard read 795 MSHA and 1,338 NHTSA printed strings and found nothing. The backstop held 383,518 record
  ids and 84 site values, and found none in the report.
- **Comma files read the same.** On each make's CSV from this run, D001's whole-text reader and D002's line reader
  gave the same columns, rows, rejects and blank lines: 6 files of 40,000 rows each.
- **The quoted narratives.** On the MSHA company files, D001's split would have rejected every row whose narrative
  holds a pipe; for c1 it would have kept 29,531 of 39,108 rows. D002 kept them all.
- The numbers say nothing about reading: the invented narratives hold their category's cue words by construction.

## Not done

- **The real run.** This machine cannot reach either source. The owner adds `docs/collective/onboard/run-002.json`
  after review, naming `docs/collective/onboard/D002-settings.json` and its sha256 above.
- **Linting the workflow.** `actionlint` and `shellcheck` are not on this machine. The workflow parses as YAML, and
  `bash -n` passes on every `run:` block.
- **What the probe did not show.** It showed the date column's shape only. How MSHA writes a quote inside a narrative
  is unknown; `CHOICE-D002.md` says what each case does. The splits print their rejected counts before the run starts.

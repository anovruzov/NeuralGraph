# Build brief for D001: the pack drafter and its test run

This is what to build so that the rule in `CHOICE-D001.md` can run. The rule decides every value; this brief says
where each piece lives and how it is checked. Where the two disagree, the rule wins and the brief is wrong.

## 0. Hard rules for the build

- **New product code** goes only in the new package `mycelic/collective/onboard/` (not hash-pinned). Download scripts
  go in `tools/onboard/`.
- **Never modify** hash-pinned code: `mycelic/collective/*.py` (top level), `mycelic/collective/detect/`, `edge/`,
  `packs/*.py`, `evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/`,
  `connectors/openfda.py` (pinned by `tests/mycelic/test_collective_x3.py`, `LIVE_CODE_HASHES_R002`, and other pins).
  The onboard package imports `packs.loader`, `packs.canonical`, `packs.connector`, `edge.extract`, `stats`,
  `jsonio` and `pilot.audit` (for its CSV reader) and changes none of them.
- **Never edit** `docs/handoff/`, any existing `CHOICE-*.md`, `docs/collective/replay/vehicles/pack/` or `pack-v2/`,
  or `lab/`.
- **Never create** `docs/collective/onboard/run-*.json`. Pushing one starts the real run; the owner adds it after
  review.
- **Python 3.11 standard library only** in product code.
- **No domain literal** in the package (section 9). Every word list, template sentence and pack default lives in the
  package's data files.
- **Deterministic:** no clock, no unseeded randomness, every output list sorted by a fixed key, JSON written with
  sorted keys.
- **Offline:** the build machine cannot reach MSHA, NHTSA or openFDA. Every test uses small synthetic exports that the
  test writes.
- **Never skip or loosen an existing test.**
- **Privacy:** no code path prints or writes a narrative, a record id, a site value or a value of a forbidden column,
  except into the drafted pack as the rule allows (category labels and floor-passing terms) and into files that stay
  inside the job.

## 1. Module layout

| File | What it holds |
|---|---|
| `mycelic/collective/onboard/__init__.py` | the package docstring: what the drafter does, pointers to `CHOICE-D001.md` and this brief |
| `mycelic/collective/onboard/__main__.py` | the CLI (section 3) |
| `mycelic/collective/onboard/exports.py` | reading an export (rule 1.1): format and delimiter detection, decoding, list columns, rejected rows by reason |
| `mycelic/collective/onboard/roles.py` | the roles file, column evidence, role inference (rule 1.2), date formats and parsing (rule 1.3), the declared-versus-inferred comparison |
| `mycelic/collective/onboard/draft.py` | categories, the floor, predicates, ids and codes (rule 1.4); the lexicon (rule 1.5); assembling the pack from the template (rule 1.6); the normalised export; the two control lexicons (rule 4) |
| `mycelic/collective/onboard/check.py` | the privacy floor (rule 1.7), the loader check, the label scan of M1 |
| `mycelic/collective/onboard/score.py` | one arm: per company, draft, controls, the held-out sample (rule 3), reading, metrics and intervals (rule 5), the matched hand-pack space (rule 2.2), the arm's criterion (rule 6) |
| `mycelic/collective/onboard/report.py` | merging both arms, the checks and the audit summary into `report.json` and `report.md`; the last guard of rule 8; the printed blocks |
| `mycelic/collective/onboard/data/defaults.json` | the drafter's parameters (rule 1): N 10, S 3, R 50, at most 199 predicates, term `df` 10, `p` 0.6, K 50, 3 letters per token, date share 0.95, and the inference thresholds of rule 1.2 |
| `mycelic/collective/onboard/data/lang/en.json` | the language file (section 2) |
| `mycelic/collective/onboard/data/template/` | the neutral template (section 2) |

Keep functions small and pure where possible. A reviewer should be able to put each rule of the choice file beside
one function. Name each function after its rule (for example `category_floor`, `term_score`, `assign_terms`,
`draw_sample`) and quote the rule's number in its docstring.

## 2. Data files

**`data/lang/en.json`**, one closed object:

- `stop_words`: an English stop list, contraction stems included (`didn`, `wasn` and so on);
- `negation`: `pre`, `post` and `terminators`, the English cues of the IT pack's vocabulary, copied;
- `joining_words`: `and`, `or` (rule 4's label split);
- `non_specific_labels`: folded phrases for other and unknown, such as `other`, `unknown`, `unknown or other`,
  `not elsewhere classified`, `none`, `no value found`, `not applicable`, `not reported`, `unspecified`;
- `months`: the twelve English three-letter month abbreviations (rule 1.3);
- `site_words` and `category_words`: header words for rule 1.2. **Not** `state`, or any other declared column name
  of either source (section 9);
- `other_bucket`: its label and its one phrase (rule 1.4);
- `scope`: the label of `export_scope` and its one alias;
- `templates`: two affirmed and one negated generator sentence with a `{term}` place, one entity sentence, at least
  five filler sentences, the fixture sentence, the question text, the pack title and disclaimer.

**`data/template/`**, read by `draft.py` and filled in:

- `vocabulary_base.json`: the `export_scope` entity type, the negation window (3) and the extraction block (the IT
  pack's values);
- `egress.json`, `detectors.json`, `questions.json`, `followups.json`, `rules.json` (no rules): the IT pack's values,
  with `export_scope` as the one egress entity type, `narrative` and `reporter` as never fields, and one T0 evidence
  packet type;
- `generator_base.json`: six synthetic sites (`site-a` to `site-f`), the rates (mixed language 0), the surface weights
  and the reporter generator.

Every pack file `draft.py` writes must pass `python -m mycelic.collective.packs.loader check`. The pack id, title,
version (0.1.0), `illustrative` true, the disclaimer from the language file, and `same_author_as_code` true go in
`pack.json`.

## 3. The CLI

`python -m mycelic.collective.onboard <command>`. Every command takes `--dry-run` (say what would be read and
written; touch nothing). Exit codes: 0 done; 1 a check failed; 2 usage or configuration error.

| Command | Arguments | Writes |
|---|---|---|
| `draft` | `--export FILE --roles FILE --pack-id ID --train-from DATE --train-to DATE --out DIR [--params FILE]` | `DIR/pack/` and `DIR/draft.json` (counts, inferred and declared roles, pack sizes, hashes) |
| `export` | `--export FILE --roles FILE --pack DIR --from DATE --to DATE --out FILE` | the normalised export as JSON lines (rule 1.6) |
| `check` | `--pack DIR --export FILE --roles FILE --train-from DATE --train-to DATE --out FILE [--params FILE]` | `check.json`: each check of rule 1.7 and the loader check, with counts; exit 1 on any failure |
| `score` | `--settings FILE --arm NAME --exports DIR --out DIR` | `DIR/arm.json` (aggregates only) and `DIR/<company>/pack/` for every drafted pack |
| `report` | `--settings FILE --arms DIR [DIR ...] --audit FILE --out DIR` | `report.json`, `report.md`; prints the blocks of section 7 |

- `score` reads `DIR/companies.json`, written by a download script: `{company label: {"file": name, ...counts}}`. It
  never reads a company id from anywhere else.
- `score` keeps per-record data (gold, predictions) in memory only. `arm.json` holds aggregates.
- A roles file is `{"language": "en", "roles": {"record_id": ..., "site": ..., "date": ..., "narrative": ...,
  "category": ..., "entities": [], "reporter": null, "forbidden": [...]}}`. `score` writes it from the settings.

## 4. The settings file and the run file

The build commits `docs/collective/onboard/D001-settings.json`, holding every value of the rule:

- `params`: equal to `data/defaults.json`;
- `bootstrap`: B 10,000 and the seed prefix `d001:boot`; the sample and permutation seed prefixes `d001:sample` and
  `d001:permute`;
- `timeout_minutes`: 180;
- `arms.msha`: the download script; the windows; the company rule (column `CONTROLLER_ID`, count 5, at least 100 test
  rows with a narrative, at least 3 training mines); 200 records per company; the 57 expected columns from
  `SOURCES.md`; the declared roles of rule 2.1; criterion C1 with margin 0.10; the audit company `c1`;
- `arms.nhtsa`: the download script; the windows; the six makes; 200 records per make; the columns `odino`, `state`,
  `received`, `components[]`, `vehicle`, `summary`, `reporter`; the declared roles of rule 2.2; the hand packs
  `pack/` and `pack-v2/`; criterion C2 with margin 0.05 against `pack/`.

A test pins each of these values as written in the choice file.

The owner later adds `docs/collective/onboard/run-001.json`:

```json
{"experiment": "D001", "settings": "docs/collective/onboard/D001-settings.json", "settings_sha256": "<64 hex>"}
```

The workflow refuses to run when the settings file's sha256 differs.

## 5. The download scripts

They stand in for a company's export. They are not product code, but they are per-field code, so the report prints
their line counts. Each has two steps, so that a download error is told apart from a run (rule 9).

- **`tools/onboard/fetch_msha.py`**
  - `download --out DIR`: fetches `Accidents.zip` and `Accidents_Definition_File.txt` (`SOURCES.md`). Prints each
    file's size and sha256, nothing else.
  - `split --settings FILE --raw DIR --out DIR`: reads `Accidents.txt`, checks that the declared role columns are in
    its header (a missing one is an error; another difference from the expected 57 columns is reported), applies
    the company rule of rule 2.1 (using `onboard.roles` to parse `ACCIDENT_DT`), and writes `c1.txt` to `c5.txt`.
    Each holds the original header and that company's original lines, unchanged. `companies.json` holds the counts
    the rule used and no controller id. `definitions.json` holds the definition file's lines for the declared
    columns. It prints counts only.
- **`tools/onboard/fetch_nhtsa.py`**
  - `download --out DIR`: fetches `COMPLAINTS_RECEIVED_2020-2024.zip`; prints size and sha256.
  - `split --settings FILE --raw DIR --out DIR`: for each make, rows as `tools/market/nhtsa_export.complaints`
    builds them, with every top-level component name kept (none folded) and a `reporter` column equal to `odino`.
    Writes `<MAKE>.csv` and `companies.json`. It prints counts only.

Neither script prints or writes a narrative, a VIN, a city, a dealer or a person's name outside the job's own working
directory. The split files are inputs to the job and are never uploaded.

## 6. The workflow `.github/workflows/onboard-run.yml`

Modelled on `vehicle-replay.yml`, with the same pinned action commits.

- **Trigger:** `push` on any branch but `main`, paths `docs/collective/onboard/run-*.json`; and `workflow_dispatch`.
  It runs the newest run file: `ls docs/collective/onboard/run-*.json | sort | tail -n 1`.
- **One job,** `runs-on: ubuntu-latest`, `timeout-minutes: 180`, `permissions: contents: read`, checkout without
  persisted credentials, Python 3.11.
- **Steps, in order:**
  1. the offline tests: `python -m unittest tests.mycelic.test_collective_onboard tests.onboard.test_onboard_fetch`
     and the guard class `OnboardGenericTests` (section 9);
  2. the settings check: the run file's `settings_sha256` against the file;
  3. **download** both sources (rule 9: a failure here is not a run);
  4. print `=== D001 RUN-START ===`; from here on, everything is the run;
  5. split both sources;
  6. `score --arm msha`, then `score --arm nhtsa`;
  7. the audit: `export` c1's test window, an outcomes file with only the header, then
     `python -m mycelic.collective.pilot.audit run` on c1's drafted pack (M4);
  8. `report`, with `if: always()`, so that a failed step still yields a report of what finished.
- **Upload** (`if: always()`): `report.json`, `report.md`, every `arm.json`, and the drafted packs whose `check`
  passed. Never the downloads, the split exports, the normalised records or `audit.json`.

## 7. The report and its printed blocks

- `report.json`: `kind` `onboard_d001_report`, `schema_version` 1, the settings sha256, the code commit, the sha256
  of every file under `mycelic/collective/onboard/` and `tools/onboard/`, the downloads' sizes and sha256, the
  download scripts' line counts, the definition lines, each arm (per company: counts, role inference, pack sizes,
  hashes, labels with corpus counts, first 10 terms per predicate, check results; per reader: micro precision, recall
  and F1 with intervals, macro F1 with interval, coverage; the differences; for NHTSA the matched space's sizes and
  the hand-pack comparisons), the audit summary (records, number of sites, weeks evaluated, alerts per channel,
  review-list length), the criteria C1, C2, M1 to M4, and the verdict.
- `report.md`: the same in tables, short sentences.
- **Printed blocks,** in the lab's style:

  ```
  === D001 report.json BEGIN lines=<n> sha256=<hex> ===
  <the file>
  === D001 report.json END ===
  ```

  and the same for `report.md`. The sha256 is of the file's bytes.
- **The last guard** of rule 8 runs before either file is written. A hit writes a report holding only the hit counts
  by kind and the verdict "withheld", and M3 fails.

## 8. Tests

`tests/mycelic/test_collective_onboard.py` (every export is synthetic and written by the test):

1. **Reading:** pipe, tab, comma with quoted fields, JSON lines; a byte-order mark; the Latin-1 fallback; a row of
   the wrong width rejected and counted; list columns.
2. **Dates:** every format of rule 1.3, two-digit years on both sides of 70, the 0.95 rule, the error when no format
   fits, rows outside the training window never read.
3. **Role inference:** synthetic exports with known roles, each rule and each tie rule; the comparison count.
4. **Categories:** the fold merge and its label rule; the floor at N - 1 rows and at S - 1 sites (left out of the value
   map); R at 49 and 50; non-specific labels to the other bucket; the 199 cap; every slug case (digit start, too short,
   reserved word, repeat, `other_category` taken first); code order.
5. **Lexicon:** a corpus built so that each `df(t)`, `df(t, c)` and `p(c | t)` is known; each threshold on both sides
   (sites 2 and 3, `df` 9 and 10, `p` just under and at 0.6); every tie rule; the K cap; tokens with digits, short
   tokens, stop words and negation words refused; a bigram only across one space and never across a sentence end; a
   forbidden value refused; the placeholder; a term planted only in test-window rows never enters.
6. **Determinism:** the same export gives byte-identical pack files; shuffled row order gives the same pack.
7. **Loading:** drafted packs from several exports (single- and multi-label) pass the loader's check; fixtures and
   generator sentences hold no export text.
8. **The normalised export:** maps through the drafted mapping with no rejection; site lower-casing; a bad site
   rejected; ISO dates.
9. **The pipeline:** the lexical extractor's predicates attach to `export_scope`; `pilot.audit run` exits 0 on a
   synthetic export spanning more than the detectors' history, with an outcomes file holding only its header.
10. **Check:** a clean pack passes; each planted violation fails its own check: a term under the floor written into
    `vocabulary.json`, a forbidden value written as a label, an 8-gram of a narrative in a fixture.
11. **Score:** micro and macro F1 and coverage on a hand-computed example; the majority prior; the permuted control
    with its fixed seed; the label-name rule (a shared part to the larger category, the placeholder); the draw and
    the de-duplication; the matched space with a synthetic hand pack that merges two names into one predicate; C1 and
    C2 at their margins; a criterion that cannot be computed fails.
12. **Report:** planted sentinels (a record id, a site value, a forbidden value, an 8-gram of a narrative) never
    appear in `report.json` or `report.md`; the last guard withholds a report with a planted hit; the block markers
    and their sha256.
13. **CLI:** `--dry-run` touches nothing; exit codes 0, 1 and 2.
14. **Settings:** `D001-settings.json` holds the choice file's values, and its `params` equal `data/defaults.json`.
15. **Workflow:** the trigger path and branch filter; the newest run file; the 180-minute limit; the sha256 check;
    the run-start marker after the downloads; the upload list (no exports, no normalised records, no `audit.json`);
    the pinned action commits equal `vehicle-replay.yml`'s.

`tests/onboard/test_onboard_fetch.py` (with `tests/onboard/__init__.py`), on a synthetic `Accidents.txt` and a
synthetic complaints archive:

- the company rule: the minimums, the ranking, the ties, fewer than five qualifying;
- company files hold the original lines unchanged; `companies.json` holds no controller id;
- the NHTSA split keeps every component name and writes `reporter` equal to `odino`;
- neither script prints a field value.

## 9. Guard additions (`tests/mycelic/test_collective_guards.py`)

The onboard package is generic code: no domain literal.

- **Lists:** add every onboard module to `STDLIB_ONLY_MODULES` and `DETERMINISTIC_MODULES`, and each subcommand to
  `CLI_MODULES`.
- **DomainLiteralTests** already scans every file under `mycelic/collective/` outside `packs/data`. Assert that the
  onboard files are among `generic_code_files()`.
- **New `OnboardGenericTests`:**
  - the terms are every column name in `D001-settings.json` (both arms' `columns` and every declared role), with and
    without a trailing `[]`, less the loader's `RESERVED` words (the pipeline's own record fields, such as
    `reporter`);
  - no onboard code file has one as an identifier or a whole string constant (`domain_literal_hits`);
  - no onboard data file has one as a JSON string or key, case-sensitive;
  - the check flags each term when injected into a copy of a module and of a data file;
  - no onboard module imports an inference module or a model client.

## 10. Docs the build changes

- `docs/collective/PACKS.md`: a short new section, "Drafting a pack from an export", that points to
  `CHOICE-D001.md` and this brief and says it is not yet measured.
- Nothing else claims a result before the run. `README.md`, `docs/strategy/YC-BRIEF.md` and `docs/collective/PILOT.md`
  are changed only after the run, with what it showed.

## 11. Done means

- every new test passes, and both full suites pass as CI runs them (`python -m pytest NeuralGraph/tests` and
  `python -m pytest tests/mycelic`), with `tests/onboard`, `tests/market` and `tests/lab` too;
- the pinned hashes are unchanged (`tests/mycelic/test_collective_x3.py` passes untouched);
- the workflow passes `actionlint` when it is available;
- `docs/collective/onboard/run-*.json` does not exist.

## As built

Built on branch `wf/d001` from the design commit `539ca60`. The rule decided every value. Where it left a detail open,
the choice below was made before any record of either source was seen. None of them changes a criterion. No code was
run on MSHA or NHTSA records: this machine cannot reach them, and every test and the dry run used invented records.

### What is where

| File | What it holds |
|---|---|
| `mycelic/collective/onboard/exports.py` | rule 1.1: decoding, format and delimiter, list columns, rejected rows by reason |
| `mycelic/collective/onboard/roles.py` | the roles file, rule 1.2's evidence and inference, rule 1.3's date formats |
| `mycelic/collective/onboard/draft.py` | rules 1.3 to 1.6 and the two control lexicons of rule 4; the normalised export |
| `mycelic/collective/onboard/check.py` | rule 1.7, the loader's check, M1's package and label scans |
| `mycelic/collective/onboard/score.py` | one arm: rules 2 to 6 |
| `mycelic/collective/onboard/report.py` | both arms, the criteria, the last guard of rule 8, the printed blocks |
| `mycelic/collective/onboard/__main__.py` | the CLI: `draft`, `export`, `check`, `score`, `report`, each with `--dry-run` |
| `mycelic/collective/onboard/data/` | `defaults.json`, `lang/en.json` and the neutral template (eight files) |
| `docs/collective/onboard/D001-settings.json` | every value of the rule; `params` equal `data/defaults.json` |
| `tools/onboard/fetch_msha.py`, `fetch_nhtsa.py` | the two-step download scripts |
| `.github/workflows/onboard-run.yml` | the run |
| `tests/mycelic/test_collective_onboard.py`, `tests/onboard/test_onboard_fetch.py` | the tests of section 8 |

### Choices the rule left open

- **Lines.** A line ends at `\r\n`, `\r` or `\n` and nothing else, so a Latin-1 `\x85` stays text. An empty line is
  no row and is counted apart. The split script cuts lines the same way, so a company file holds whole lines.
- **JSON lines.** The columns are the keys in the order they first appear. A nested object, or a list under a key
  without `[]`, rejects the row as `nested_value`. Numbers become their text.
- **Inference evidence.** In a list column every element is a value. Every tie not named by the rule goes to the
  leftmost column.
- **A category spelling longer than 200 characters**, or more than 1,000 spellings past the floor, cannot be a value
  map key. The draft then fails with an error; it never drops a spelling silently.
- **Whole words.** "A value occurs in a term as whole words" is `find_bounded` on folded text. An index by the
  value's first alphanumeric run makes it fast; it finds exactly what `find_bounded` finds.
- **The normalised export** uses the keys `record_ref`, `site`, `received_date`, `codes`, `narrative` and `scope`
  (plus `entities` and `reporter` when declared). The mapping requires nothing, so a row without a narrative still
  carries its codes.
- **The check** reads site, record-id and forbidden values from every row of the export, not only the training rows.
  It recounts term presence from the folded sentences directly, not through the drafter's candidate terms.
- **The generator base** takes the IT pack's rates (mixed language set to 0) and surface weights. Its six sites and
  six reporters are invented names, not the IT pack's reporters, so that no field's words sit in the template.
- **The controls** are the drafted pack with each lexicon replaced; its generator and fixtures stay. The loader checks
  each control before it reads.
- **Reading.** A sampled record whose site falls outside the pipeline's site pattern is read with a fixed site
  (`reader-site`): the reader reads only the narrative. A row the mapping rejects reads as nothing and is counted.
- **The gold** is the record's filed values that fold equal to a specific predicate's category.
- **De-duplication** runs over the test-window records with a narrative and a record id. "Smallest record id" is the
  smallest string. A repeated record id keeps its first row.
- **Order of the pooled records:** companies in label order, then each company's draw in the order `sample` returns.
- **The best control** on a tie of micro F1 is the first of majority prior, permuted labels, label names.
- **Per-company intervals** use the arm's seed.
- **Numbers written** to `arm.json` and the report are rounded to four places. Every decision uses full precision;
  C1's margin compares the exact difference of two fractions.
- **The last guard** matches record ids, site values and forbidden values as written, case-sensitive, as whole words.
  It matches narrative 8-grams on folded tokens. Its sentinels come from every row of both arms' exports. If it
  cannot read an export, it cannot clear the report: the report is withheld as for a hit, and M3 fails.
- **M4's "has a summary"** is read as "the channel ran": no reason and an alert timeline. This keeps the column name
  `summary` out of the package (M1).
- **Definition lines:** the first line naming a column, plus the following lines up to a blank line, a line naming
  another column, or eight lines. The settings list the declared columns and `ACCIDENT_TYPE`, so that section 2.1's
  reading of both category columns can be checked.
- **Exit codes:** `score` exits 1 when a company was not drafted or a pack failed its check; `report` exits 1 unless
  the verdict is pass. Every workflow step after the run-start marker runs when an earlier one failed. A job stopped
  by its time limit writes no report: that run fails.

### Conflicts with the rule as written, for the owner

The code follows the rule's words in each case. Each can change a result, so the owner may want an amendment before
any run.

1. **October in `D-MON` dates.** Rule 1.3 cuts a date at its first space or `T`. An upper-case `OCT` holds a `T`, so
   `04-OCT-2021` becomes `04-OC` and does not parse. If the MSHA date column were written that way, every October
   date would fail to parse, the column could miss the 0.95 share, and the drafts would fail. The probe did not show
   the column's format (`SOURCES.md`).
2. **A category spelling equal to a forbidden value.** Rule 1.4 puts every category that passes the floor into the
   value map. Check 1.7.3 then fails the pack if any pack string folds equal to a forbidden, site or record-id value.
   A missing-value marker shared by the category column and a forbidden column (such as `?`) would fail M3 by
   construction.
3. **The last guard and common words.** A forbidden value of four or more characters with a letter that is also a
   common word (such as `OTHER` in a manufacturer column) withholds the report if a printed category label is that
   word.
4. **A bigram longer than 64 characters** would pass rule 1.5 and fail the loader's term limit (M2). It needs two very
   long words side by side in at least ten records at three sites.

### Tests

- `tests/mycelic/test_collective_onboard.py`: reading, dates, role inference, categories, the lexicon, determinism,
  loading, the normalised export, the pipeline, the check, scoring, the controls, the sample, the matched space, the
  arm, the report, the CLI, the settings and the workflow. The workflow's structure test needs PyYAML and skips
  without it (the run's own job has none); its textual tests do not.
- `tests/onboard/test_onboard_fetch.py`: the company rule, unchanged company lines, no controller id or value
  printed, definition lines, the NHTSA split, and both downloads with a fake fetcher.
- `tests/mycelic/test_collective_guards.py`: the eight onboard modules join the stdlib-only, CLI and determinism lists;
  a new `OnboardGenericTests` holds M1's static checks.

Counts from `python -m pytest <file> -q` on this branch:

| File | Result |
|---|---|
| `tests/mycelic/test_collective_onboard.py` | 74 passed, 19 subtests passed |
| `tests/onboard/test_onboard_fetch.py` | 8 passed, 2 subtests passed |
| `tests/mycelic/test_collective_guards.py` | 83 passed, 721 subtests passed |
| `tests/mycelic/test_collective_x3.py` (the hash pins, untouched) | 32 passed, 141 subtests passed |

The full suites on the build commit, each by `python -m pytest <dir> -q`:

| Suite | On this branch | On `539ca60` |
|---|---|---|
| `tests/mycelic` | 1620 passed, 1 xfailed, 45514 subtests passed | 1540 passed, 1 xfailed, 45407 subtests passed |
| `NeuralGraph/tests` | 264 passed, 1 skipped, 305 subtests passed | not run |
| `tests/onboard` and `tests/market` | 95 passed, 35 subtests passed | not run |
| `tests/lab` | 578 passed, 17372 subtests passed | not run |

No test failed in any of these runs.

`actionlint` 1.7.12 reports nothing on the workflow. It ran without `shellcheck`, which this machine lacks, so the
`run:` scripts were not linted.

### The dry run

`synth.py` (kept outside the repository) wrote invented records in each source's column layout: an MSHA-style
pipe file of 275,220 rows over 14 invented controllers, and an NHTSA-style tab file of 350,157 rows over seven makes.
The split counted 40,000 complaints for each of the six makes the settings name. The steps then ran as the workflow
runs them, after the downloads, on one machine:

| Step | Seconds |
|---|---|
| split, MSHA layout | 6.0 |
| split, NHTSA layout | 14.8 |
| score, MSHA arm (5 companies, 1,000 sampled records) | 185.2 |
| score, NHTSA arm (6 makes, 1,200 sampled records, two hand packs) | 362.1 |
| export of c1's test window | 1.6 |
| pilot audit of c1 | 19.7 |
| report and last guard | 55.6 |

The report step was timed again alone after its last change (`rerun_report.py`, also outside the repository); the
other rows are from the full run (`run_pipeline.py`). The report's verdict was pass, with every criterion met. The
largest process used 654 MB. The bootstrap intervals (B = 10,000) take most of the scoring time. The numbers say
nothing about reading: the invented narratives hold their category's cue words by construction.

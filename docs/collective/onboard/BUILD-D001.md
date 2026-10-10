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
  passed. Never the downloads, the split exports, the normalised records or `audit.json`. A withheld report, or no
  report, uploads alone: no arm file and no pack (amendment A5).

## 7. The report and its printed blocks

- `report.json`: `kind` `onboard_d001_report`, `schema_version` 1, the settings sha256, the code commit, the sha256
  of every file under `mycelic/collective/onboard/` and `tools/onboard/` and of every module the download scripts
  import from `tools/` (amendment A8), the downloads' sizes and sha256, the line counts of that download code, the
  definition lines, each arm (per company: counts, role inference, pack sizes,
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
- **The last guard** of rule 8 runs before either file is written. As amended (A5), it reads per arm the strings the
  report prints that came from records (labels, ids, terms, error texts) against that arm's exports. A hit writes a
  report holding only the hit counts by kind and the verdict "withheld", and M3 fails.

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

Two reviews then found that the rule as written failed privacy checks by construction. The rule was amended before
any run (`CHOICE-D001.md`, "Amended before any run, 2026-10-09", commit `ba7b754`), and the code was fixed on
`wf/d001-fix`. This section describes the code as it now stands; "The review findings" below lists each finding.

### What is where

| File | What it holds |
|---|---|
| `mycelic/collective/onboard/exports.py` | rule 1.1: decoding, format and delimiter, list columns, rejected rows by reason |
| `mycelic/collective/onboard/roles.py` | the roles file, rule 1.2's evidence and inference, rule 1.3's date formats |
| `mycelic/collective/onboard/draft.py` | rules 1.3 to 1.6, the one refusal of amendment A2 (`Refusal`, `export_refusal`), the two control lexicons of rule 4; the normalised export |
| `mycelic/collective/onboard/check.py` | rule 1.7 as amended (A3: the derived strings and the template rebuild), the loader's check, M1's package and label scans |
| `mycelic/collective/onboard/score.py` | one arm: rules 2 to 6 |
| `mycelic/collective/onboard/report.py` | both arms, the criteria, the last guard of rule 8 as amended (A5), the printed blocks |
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
- **The refusal** (A2) is one class, `draft.Refusal`, built by `draft.export_refusal` from every row's record-id, site
  and forbidden values. The drafter, the check and the last guard call it. Lengths are counted after folding. The
  check recounts term presence from the folded sentences directly, not through the drafter's candidate terms.
- **A refused category** is found by planning the predicates, refusing every category one of whose strings (its
  spellings, its label cut to 80 and to 120 characters, its id, its placeholder) is refused, and planning again
  without them until nothing more is refused. A removal can change other ids and the 199 cap.
- **The template rebuild** (A3, 3b) compares each pack file's parsed JSON with what `check.rebuild_pack` makes, so a
  pack written with other spacing but the same content passes. A pack the rebuild cannot read fails.
- **Loader errors** keep the failing file and the problem, without the JSON path, which can name a predicate or a
  category spelling. A company whose drafted pack does not load is reported with the failing file only.
- **A pack that failed the floor** keeps counts only in `arm.json` (`strings_withheld`): no label, id or term. Its
  `draft.json` and `check.json` stay inside the job.
- **M1's download code** is listed per arm in the settings (`download_code`). A test checks that each list is its
  script's import closure within `tools/`.
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
  C1's margin compares the exact difference of two fractions. A criterion's deciding values sit unrounded under
  `exact`, with each comparison's outcome, and the report prints them in full beside the comparison (A8).
- **The last guard** (A5) reads, per arm, these printed strings of each arm file: the passing-floor labels, each
  predicate's label and id, its first terms (a placeholder read as an id), the majority prior's id, a company's error
  text and a loader error. Terms go through the term rule, the rest through the string rule, against the refusal
  built from every row of every company export of that arm. The 8-gram scan runs on the parsed strings. If it cannot
  read an export, it cannot clear the report: the report is withheld as for a hit, and M3 fails. The withheld report
  gives each arm's hit counts by kind and names the kinds found, never a string.
- **M4's "has a summary"** is read as "the channel ran": no reason and an alert timeline. This keeps the column name
  `summary` out of the package (M1).
- **Definition lines:** the first line naming a column, plus the following lines up to a blank line, a line naming
  another column, or eight lines. The settings list the declared columns and `ACCIDENT_TYPE`, so that section 2.1's
  reading of both category columns can be checked.
- **Exit codes:** `score` exits 1 when a company was not drafted or a pack failed its check; `report` exits 1 unless
  the verdict is pass. Every workflow step after the run-start marker runs when an earlier one failed. A job stopped
  by its time limit writes no report: that run fails.

### Conflicts with the rule as written, and how they were settled

The build first followed the rule's words. Each conflict below could change a result. The amendment before any run
settles all of them.

1. **October in `D-MON` dates.** Rule 1.3 cuts a date at its first space or `T`. An upper-case `OCT` holds a `T`, so
   `04-OCT-2021` becomes `04-OC` and does not parse. If the MSHA date column were written that way, every October
   date would fail to parse, the column could miss the 0.95 share, and the drafts would fail. The probe did not show
   the column's format (`SOURCES.md`). **Settled by A1:** the cut is at a space, or at a `T` a digit follows.
2. **A category spelling equal to a forbidden value.** Rule 1.4 puts every category that passes the floor into the
   value map. Check 1.7.3 then fails the pack if any pack string folds equal to a forbidden, site or record-id value.
   A missing-value marker shared by the category column and a forbidden column (such as `?`) would fail M3 by
   construction. **Settled by A2:** such a category is left out of every pack file and counted.
3. **The last guard and common words.** A forbidden value of four or more characters with a letter that is also a
   common word (such as `OTHER` in a manufacturer column) withholds the report if a printed category label is that
   word. **Settled by A5:** the guard reads only the printed strings that came from records, per arm.
4. **A bigram longer than 64 characters** would pass rule 1.5 and fail the loader's term limit (M2). It needs two very
   long words side by side in at least ten records at three sites. **Settled by A4.**
5. **The template's own strings against record values** (found by the reviews). Check 1.7.3 compared every pack
   string, keys included. NHTSA's sites are lower-cased states, and Idaho's `id` equals the key `id` of `pack.json`:
   one complaint from Idaho failed every NHTSA pack. **Settled by A3:** only the strings derived from records are
   compared, and the rest of the pack must rebuild from the template.
6. **Two refused sets** (found by the reviews). The drafter refused training-row values; the check refused every
   row's. A term learned honestly failed the floor when a test-window row held it as a forbidden value, and a refusal
   was never counted. **Settled by A2:** one refusal from every row, and `draft.json` counts refused terms and
   categories.
7. **Names and vehicles** (found by the reviews). A surname inside a multi-word name passed every check, and NHTSA's
   vehicle column was not forbidden, so a model name could become a printed term. **Settled by A2** (a word of a
   multi-word forbidden value) **and A6** (`vehicle` forbidden).

### Tests

- `tests/mycelic/test_collective_onboard.py`: reading, dates, role inference, categories, the lexicon, determinism,
  loading, the normalised export, the pipeline, the check, scoring, the controls, the sample, the matched space, the
  arm, the report, the CLI, the settings and the workflow. The workflow's structure test needs PyYAML and skips
  without it (the run's own job has none); its textual tests do not. Section 16 holds the amendment's tests:
  `RefusalTests`, `AmendedDraftTests`, `AmendedCheckTests`, `GuardTests`, `ExactPrintingTests`, `MatchedGoldTests`,
  `ArmWiringTests`, `GoldTests`, `DownloadCodeTests`, `AmendmentRecordTests` and `WorkflowAmendmentTests`. The last
  runs the workflow's own collect script, read from the YAML text, on a withheld, a missing and a cleared report.
- `tests/onboard/test_onboard_fetch.py`: the company rule, unchanged company lines, no controller id or value
  printed, definition lines, the NHTSA split, both downloads with a fake fetcher, and `HonestFetchTests`: the
  User-Agent names the run, one request per file, no retry after a refusal.
- `tests/mycelic/test_collective_guards.py`: the eight onboard modules join the stdlib-only, CLI and determinism lists;
  a new `OnboardGenericTests` holds M1's static checks.

Three existing tests changed with the amendment, each to assert the amended rule as strictly as before:
`test_the_part_before_the_first_space_or_a_t_before_a_digit_is_parsed` (A1; it asserted that `4-OCT-2021` does not
parse), `test_rows_outside_the_training_window_are_read_only_at_their_date` (A2: outside the window the date and the
identifying columns are read, never a narrative or a category) and the settings test (A2, A4, A6, A8 values). The
check's result keys `fold_equal` and `term_contains` became `refused_strings` and `refused_terms`, and two report
tests now plant their hit in the arm file, where the guard reads.

Counts from `python -B -m pytest <file> -q -p no:cacheprovider` on `wf/d001-fix`:

| File | Result |
|---|---|
| `tests/mycelic/test_collective_onboard.py` | 116 passed, 35 subtests passed |
| `tests/onboard/test_onboard_fetch.py` | 11 passed, 6 subtests passed |
| `tests/mycelic/test_collective_guards.py` | 83 passed, 721 subtests passed |
| `tests/mycelic/test_collective_x3.py` (the hash pins, untouched) | 32 passed, 141 subtests passed |

The full suites, each by `python -B -m pytest <dir> -q -p no:cacheprovider`:

| Suite | On `wf/d001-fix` | On `8a3ba97` (as this file recorded it then) |
|---|---|---|
| `tests/mycelic` | 1662 passed, 1 xfailed, 45530 subtests passed | 1620 passed, 1 xfailed, 45514 subtests passed |
| `NeuralGraph/tests` | 264 passed, 1 skipped, 305 subtests passed | 264 passed, 1 skipped, 305 subtests passed |
| `tests/onboard` and `tests/market` | 98 passed, 39 subtests passed | 95 passed, 35 subtests passed |
| `tests/lab` | 578 passed, 17372 subtests passed | 578 passed, 17372 subtests passed |

No test failed in any of these runs. The `tests/lab` run was on the committed fix (`ef66192`): its E1
preregistration refuses a tree with uncommitted collective code, and a first run before the commit failed for that
reason alone. `actionlint` and `shellcheck` are not installed on this machine, so the workflow was not linted after
the fix; the earlier `actionlint` 1.7.12 run was on `8a3ba97`.

**The mutation run.** `run_mutants.py` (kept outside the repository) applied one textual mutant at a time to a copy of
this tree and ran the two onboard test files with `python -B -m pytest -q -x`. It holds the reviews' surviving
mutants, adapted to the amended code, and at least one for each amendment: 44 mutants. The first pass left five alive
(the guard not reading the site column, the gold taking every filed value, the guard reading terms by the string
rule, the matched gold from names in C only, no matched names beside C2). Tests were added for each, and a second
pass of all 44 killed every one.

### The dry run

`synth2.py` (kept outside the repository) is the earlier `synth.py` with four changes that reach the amendment's
paths: MSHA dates written in upper-case D-MON-YYYY (`04-OCT-2021`); Idaho and Maine among the NHTSA states; equipment
cells that read `Other`, `UNKNOWN`, `FORD` or `?`; and `?` filed as a classification too. It wrote an MSHA-style pipe
file of 275,220 rows over 14 invented controllers, and an NHTSA-style tab file of 349,896 rows over seven makes. The
split counted 40,000 complaints for each of the six makes the settings name. The steps then ran as the workflow runs
them, after the downloads, on one machine (`run_pipeline.py`, also outside the repository):

| Step | Seconds |
|---|---|
| split, MSHA layout | 5.7 |
| split, NHTSA layout | 12.0 |
| score, MSHA arm (5 companies, 1,000 sampled records) | 169.3 |
| score, NHTSA arm (6 makes, 1,200 sampled records, two hand packs) | 279.3 |
| export of c1's test window | 1.4 |
| pilot audit of c1 | 15.7 |
| report and last guard | 21.1 |

- Every step exited 0 and the verdict was pass. The largest process used 774 MB.
- The MSHA date column parsed as D-MON-YYYY, with no row rejected.
- FORD's export held 2,041 rows from Idaho, and every NHTSA pack passed its floor.
- Each MSHA company left out two categories, `OTHER` and `?`, each equal to an equipment cell, and refused 305 terms
  that passed the floor, all by the name-word rule: the invented equipment and operator names share invented
  syllable words with the invented narratives. Each make refused 6 to 8 terms, words of its invented vehicles.
- The last guard read 785 MSHA and 1,338 NHTSA printed strings and found nothing; the equipment cells `Other`,
  `UNKNOWN` and `FORD` withheld nothing.
- The numbers say nothing about reading: the invented narratives hold their category's cue words by construction.

### The review findings

Two adversarial reviews of `8a3ba97` found the issues below. Each fix has a test.

| Finding | What changed | Pinned by |
|---|---|---|
| Check 1.7.3 compared the template's keys and words with record values; Idaho's `id` failed every NHTSA pack (blocking) | A3: only record-derived strings are compared; the rest must rebuild from the template | `test_real_state_codes_as_sites_pass_the_floor`, `test_a_forbidden_value_equal_to_a_template_word_or_key_passes`, `AmendedCheckTests` |
| The last guard pooled both arms and matched the whole report, case-sensitive: one cell spelled `Other` or `FORD` withheld it (blocking) | A5: per arm, only printed record-derived strings, folded | `GuardTests`, `test_a_planted_hit_withholds_the_report` |
| The drafter and the check refused different values, and a refusal was silent (blocking) | A2: one `Refusal` from every row; refused terms and categories counted | `RefusalTests`, `test_a_forbidden_value_only_in_test_rows_refuses_the_term_in_drafter_and_check_alike` |
| A withheld report still uploaded `arm.json` and the packs | The collect step uploads the report alone when it is withheld or missing | `WorkflowAmendmentTests` |
| A word of a multi-word name passed every check | A2's name-word rule; a term is refused when any of its words is | `RefusalTests.test_name_words`, `test_a_surname_inside_a_name_is_never_learned` |
| Terms of a pack that failed the floor were printed; the guard read escaped JSON and matched as written | `withhold_strings`; the guard reads parsed, folded strings | `test_a_failed_floor_company_prints_no_label_id_or_term`, `test_the_guard_reads_parsed_strings_and_folds` |
| NHTSA's vehicle column was not forbidden | A6 | `test_the_vehicle_column_refuses_model_words`, `SettingsTests` |
| Upper-case October dates did not parse (conflict 1) | A1 | `DateTests` |
| A marker filed as a category and in a forbidden column failed M3 (conflict 2) | A2: the category is left out | `test_a_category_equal_to_a_forbidden_marker_is_left_out_not_failed` |
| A bigram over 64 characters failed the loader (conflict 4) | A4 | `test_a_term_longer_than_the_loader_allows_is_no_candidate` |
| C2 read as "no worse than a hand-built pack"; matched names not printed; `pack-v2/`'s gold was one-sided | A7 and A8 | `MatchedGoldTests`, `ExactPrintingTests` |
| M1 neither counted nor hashed the `tools/market` code the NHTSA split imports | A8: `download_code` per arm | `DownloadCodeTests` |
| Criteria printed to three places beside "Passes: no" | A8: an unrounded `exact` block with each comparison | `ExactPrintingTests` |
| The language file's words were chosen knowing both sources; C1's interval read as general; label names read as a hand start | A9 and A8: the declaration, and notes beside the numbers | `ReportTests.test_sentinels_never_appear`, `AmendmentRecordTests` |
| The privacy checks, the controls' wiring and the honest-fetch rules were not pinned | New tests | the mutation run above |

### Not fixed

- **A unigram label-word variant of the label-names control** (optional in the review). It would add a control and so
  could change C1's best control. Only the wording changed (A8).
- **A company-stratified bootstrap for C1** (optional in the review). The report says the interval is over these
  companies' records and prints each company's drafted and best-control F1 side by side.
- **Linting the workflow:** `actionlint` and `shellcheck` are not on this machine.
- **The real run:** this machine cannot reach either source. The owner adds `docs/collective/onboard/run-001.json`
  after review, naming `docs/collective/onboard/D001-settings.json` and its sha256 as amended.

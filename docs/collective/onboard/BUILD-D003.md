# Build note for D003: what was built

D003 (`CHOICE-D003.md`) asks whether a pack drafted from the FDA device adverse-event reports that name one maker's
devices reads that maker's later reports. The rule was committed alone (`55f4c71`) and amended before any code
(`7f4766d`, E1 to E18; sha256 `78dd1176087a1da4575c0f1b714ac2345eaa47022ad3193108ffe0e8cb24752a`). This note says what
was built to it, on branch `wf/d003` from `7f4766d`:

- `d4052b0`: D002's outputs on a fixed synthetic input, recorded from the code D002 ran, before any onboard change;
- `445b738`: the build;
- the commit holding this note.

**No device data was read.** The build made no request to `api.fda.gov`. Every test and the dry run read invented
data; the openFDA-shaped archive is served on `127.0.0.1` (`tests/onboard/openfda_fake.py`). The build saw no
narrative, problem-code distribution or category count of any device maker. `run-003.json` does not exist: the owner
adds it after review (section 12).

**Deviations from the rule: none.** Where the rule leaves a detail to the build, the choice is listed under
"Choices the rule left to the build". None changes a value the rule fixes.

**One difference from the build brief, not from the rule.** The brief named the download script
`tools/onboard/fetch_openfda_device.py`. Section 12 names it `tools/onboard/fetch_openfda.py`, and so do the settings'
`download_script` and `download_code` (M1) and the workflow. The rule's name is used.

## What was built, file by file

| File | What it is |
|---|---|
| `tools/onboard/fetch_openfda.py` | New. Sections 2.1 to 2.6 with E5 and E9 to E13: select, check, walk, split, print counts. See "The fetch". |
| `docs/collective/onboard/D003-settings.json` | New. Section 12 as amended. sha256 `9b06b12c9697a532448c33e6a59499b9af7a4bebfc80c99deee7c5e6f5b4b546`. |
| `docs/collective/onboard/d003-hand/device_quality/` | New. `device_quality` with the 22-name map of `lab/packs/device_quality_bd`, changed only as E1 (c) allows. |
| `docs/collective/onboard/d003-hand/device_quality_own/` | New. The same, with `device_quality`'s own six-name map. |
| `.github/workflows/onboard-d003.yml` | New. Section 12 with E14. See "The workflow". |
| `mycelic/collective/onboard/draft.py`, `check.py`, `score.py`, `report.py`, `__main__.py` | P1 to P13 (E18), each off unless the settings turn it on. See "The onboard package". |
| `tests/onboard/test_d002_reproducible.py` and `tests/onboard/data/d002_reproducible/` | New (`d4052b0`). Section 11's test. |
| `tests/onboard/openfda_fake.py` | New. The invented openFDA-shaped archive and its local server. |
| `tests/onboard/test_onboard_d003.py` | New. 73 tests: fetch, split, settings, hand copies, P1 to P13, full arm, guard, workflow, frozen files. |
| `tests/mycelic/test_collective_guards.py`, `tests/mycelic/test_collective_packs.py` | One change each, because a D003 file now exists. See "Tests". |

**Unchanged on purpose:** every hash-pinned file (`mycelic/collective/*.py` at the top level, `detect/`, `edge/`,
`packs/*.py`, `evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/`, `connectors/openfda.py`); the
language file, the template and the pack data under `mycelic/collective/packs/data/`; `docs/collective/replay/`;
`CHOICE-D001.md`, `CHOICE-D002.md`, `CHOICE-D003.md`, `D001-settings.json`, `D002-settings.json`, `run-001.json`,
`run-002.json`; `onboard-run.yml`; `fetch_msha.py` and `fetch_nhtsa.py`; `stats`.

## The fetch

`python tools/onboard/fetch_openfda.py fetch --settings FILE --out DIR [--base-url URL] [--dry-run]`. One command
selects, fetches and splits. Every number it uses comes from the settings.

- **HTTP (2.1, E9).** The pinned connector's `build_opener` and `_get`, unmodified. `CountingOpener` wraps the
  opener: its `open()` counts every attempt, retries included, and raises `AttemptCap` before the 801st. `AttemptCap`
  is not an `OSError` or an `HTTPException`, so `_get` does not retry it. A query is encoded as the connector's
  `page_url` encodes one (`urlencode` with `quote_plus`). Each request carries D002's User-Agent. openFDA's 404
  `NOT_FOUND` is read as no report; any other answer that is not 200 stops the fetch with its status and `error.code`.
- **The companies (2.2, E12).** Two count requests (`device.manufacturer_d_name.exact`, limit 100, one per window,
  dates written `YYYYMMDD`). The candidates are the names in both lists, most training-window reports first, ties by
  the name's UTF-8 bytes. Each is excluded by the first exclusion it meets: a placeholder (folded by
  `packs.canonical.folded`, against the 22 placeholder values of `field-coverage.json`, or no letter); not searchable
  (a double quote, a backslash, a control character, or more than 120 characters); a variant of a name already
  qualifying (`normalise_name` of `tools/market/openfda_establishments.py`); too few reports (under 2,000 or 1,000).
  The first five qualifying are `d1` to `d5`. Fewer than three stops the fetch.
- **The exact-name check (2.3, E10).** Per company and window, the `.exact` search's total against the list's count;
  asked once more when it differs; a company that still differs is dropped before its days are walked, not replaced,
  and stays in `makers[]`.
- **The walk (2.3, E10, E11).** The window's days, shuffled by `random.Random("d003:days:<window>:<label>")`. A day's
  first page gives its total `T`; a day of more pages than the per-day cap (14 or 10) or than the budget left is too
  large, and its first page is discarded unread. A fetched day is incomplete when its distinct keys are not `T`, a key
  repeats, a record was received on another day, or a record names the company in no device entry. It is fetched
  once more if the budget allows, then kept or left out. The walk stops when the budget (70 or 50 pages) is spent.
- **The minimums (2.3, E11, E12).** Reports with a "Description of Event or Problem" entry: 1,000 training and 500
  test; 5 kept days per window. A company under one is not used and not replaced. Fewer than three left stops the
  fetch.
- **The export (E5).** `d<i>.jsonl`: one object per kept report, keys in the settings' column order
  (`mdr_report_key`, `date_received`, `product_problems[]`, `mdr_text`, `received_day`, `maker_entity`, `makers[]`),
  nothing else. `mdr_text` is `n1_narratives.narrative` with the one text type, absent when empty. A record with a
  device entry naming another manufacturer is dropped and counted.
- **What it writes.** The exports; `companies.json` (per used label: file, list counts, kept days, kept reports,
  reports with a narrative); `source.json` (the windows, the selection counts, how many used, why each other label is
  not used), which the score copies into `arm.json`; `fetch.json`, the source record, with every count and each
  export's bytes and sha256. No file holds a name.
- **What it prints (E13).** One JSON line per item: the selection, each company's list counts and check result, each
  walk's counts, the kept days with their totals and that list's sha256, each export's bytes, sha256 and use, the
  attempts and calls. An error prints as its class name only, with the HTTP status and `error.code` for an HTTP error.
  Exit 1 for a stated stop, 2 for any other exception, 0 when done.
- **The download code (M1).** The fetch and every module it imports beyond the standard library and the onboard
  package: `connectors/openfda.py`, `experiments/n1_narratives.py`, `packs/canonical.py` and
  `tools/market/openfda_establishments.py`. 2,124 lines in all; the report gives each file's count.

## The onboard package (P1 to P13)

Each change is switched on by a key that `D002-settings.json` does not have. Without it the code runs D002's path.

| Item | Where | The switch | What it does |
|---|---|---|---|
| P1 | `score.criterion_specs`, `run_arm`, `_finish_d003`; `report.arm_criterion`; `__main__._score` | `criterion` is a list | Each criterion computed on one sample; printed in order. |
| P2 | `score.criterion_reach` | C2's `min_records` | Fewer records with a nonempty gold: not decided, failed (E8). |
| P3 | `report.listed_criteria`, `needs_audit`; `__main__` | top-level `criteria` | The verdict takes the list; `--audit` needed only with M4 or no list. |
| P4 | `draft.with_non_specific` | `params.non_specific_labels` | The nine FDA terms join the language's non-specific labels, folded. |
| P5 | `report.report_text`, `render(doc, settings)` | `report_text` | Source-specific sentences from the settings; else D002's (`D002_TEXT`). |
| P6 | `score.label_echo`, `word_echo`, `masked_repeats`, `_subsets`; `report._subset_rows` | `reported.echo_free`, `masked_repeats` | C1's and C2's comparisons on the records with no echo or repeat. |
| P7 | `score._hand_reach`, `most_frequent_reached`, `criterion_reach`; `report._space_rows` | C2's `space: "reach"` | E2's space, both record-blind readers, superiority over three. |
| P8 | `score.cluster_members`, `cluster_counts`, `cluster_f1`, `cluster_difference`, `compare` | `interval: "cluster"` | E3: `stats` on (company, received day) cluster triples; record intervals beside. |
| P9 | `draft.label_words`, `label_word_lexicon`, `refused_label_words`, `set_prior`; `score._company` | C1's `controls` | E6's label-words and set-prior controls. |
| P10 | `score.read_records_counted`; `criterion_reach`; `report._guard_rows` | `reported.reader_guard`, C2's `guard` | E1's counts per company and reader; C2 not decided when the deciding copy rejects a row or leaves one without a primary entity. |
| P11 | `score.single_narrative_terms`; `report._company_extra_rows` | `reported.single_narrative_terms`, `kept_days` | E4's and E7's measures; kept days per company beside C1 and C2. |
| P12 | `check.earlier_settings`, `column_names` | `m1_settings` | D001's and D002's column names join M1's package and label checks and rule 1.7. |
| P13 | `check.exempt_labels`, `within_spellings`, `check_pack`; `report.company_hits`, `exempt_counts` | `params.non_specific_labels` | The nine terms left out of both n-gram scans; hits within value-map spellings counted. |

- `score.read_records` keeps its signature and result: it calls `read_records_counted` and returns the rejected
  count, as before.
- C1 (P1, P8, P9) passes when the exact difference against the best of five controls is at least 0.10 and the
  cluster interval's lower end is above 0. The record interval is printed beside it, labelled as deciding nothing.
- C2 (P7) passes on superiority over `device_quality`, `all_reached` and `most_frequent_reached` together, by the
  cluster interval, with 100 records with a nonempty gold. The non-inferiority comparison (-0.05) is printed under
  the label "non-inferiority, reported only, decides nothing".
- `report.render(doc)` with no settings, as D002's code called it, still gives D002's text. `run_report` passes the
  settings only when they hold `report_text` or `criteria`.

**D002 stays reproducible (section 11).** `tests/onboard/test_d002_reproducible.py` builds a fixed synthetic input in
D002's two layouts (MSHA's quoted pipe-delimited accident file, NHTSA's tab-delimited complaint archive, every value
invented) and runs the D002 workflow's steps after its downloads with `D002-settings.json`: both splits, both arms
scored, the export and pilot audit of the audit company, and the report. The expected `arm.json` of both arms,
`report.json` and `report.md` were recorded at `9070788`, in a separate worktree, before any onboard change
(`d4052b0`). On this build they are byte-identical except `code_commit`, `code_files`, `files`, `code_hash`, the
report line that prints them, and the test's working path. The test also checks that every step exits as it did,
that the run is a full D002 run (3 MSHA companies, 6 NHTSA makes, all six criteria), and that nothing else is blanked.
It passed after every change to the package.

## The hand copies (E1 (c))

Both copies are `mycelic/collective/packs/data/device_quality/`, file for file, with these changes and no other.
`HandCopyTests` rebuilds each expected file from the pack and compares; every other file is byte-identical.

- `mapping.json` (E1 (a), E5): record ref `mdr_report_key`, site `received_day`, received date `date_received`
  (`yyyymmdd`), codes from `product_problems[]` through the 22-name map (`device_quality`) or the six-name map
  (`device_quality_own`), narrative `mdr_text`, language null, reporter null, origin fields null, persons none, entity
  `maker` from `maker_entity`, primary entity type `maker`, required `narrative`.
- `vocabulary.json` (E1 (b)): the added entity type `maker`, "Company label (D003)", ids `D1` to `D5`, alias-only
  (no id format), upper case, no egress.
- What the loader requires before the new mapping and type load:
  - `aliases.json`: `d003 maker d1` to `d003 maker d5`, one per id;
  - `generator.json`: the universe's `maker` ids, `fill_rates` `{"maker": 1.0}`, `persons` emptied, and the
    `{person:...}` slot removed from the two narrative templates that held one. With no persons in the mapping (6.2
    (a)), the loader refuses such a slot ("unknown person slot");
  - `egress.json`: the `persons.*` entries of `never_fields` removed, since the loader refuses a field the mapping no
    longer has;
  - `fixtures/records.jsonl`: each fixture's `entities` set to `{"maker": []}` and `persons` to `{}`, the shape the
    new mapping gives a record.
- The predicates, negation, extraction rules and codes are `device_quality`'s (`test_the_reading_vocabulary_is_unchanged`).
- E1's test: a row in D003's layout with `maker_entity` `D1` and the word "cracked" yields `crack` through each copy
  by the score's own reading function, with every guard count 0; the same row with `d1` yields nothing and counts
  one record without a primary entity, one structured value unresolved and one `no_entity` drop.

## The settings

`D003-settings.json` holds what section 12 and "The settings, as amended" list, and `SettingsTests` checks each
value against the rule:

- experiment `D003`, kind `onboard_d001_settings`, choice `CHOICE-D003.md`, language `en`, 180 minutes, criteria
  `C1, C2, M1, M2, M3`, `m1_settings` D001's and D002's settings files;
- D002's parameters unchanged (`floor_sites` 3), plus the nine non-specific labels, which equal
  `field-coverage.json`'s `generic_problems`;
- D002's bootstrap (B 10,000, alpha 0.05) with seeds `d003:boot`, `d003:sample`, `d003:permute`;
- one arm, `maude`: E5's seven columns and roles; training 2021-01-01 to 2022-12-31, test 2023-01-01 to 2024-12-31;
  200 per company; the company rule (top 100, five labels, at least three, 120 characters, 2,000 and 1,000 reports,
  the 22 placeholders, equal to `field-coverage.json`'s `placeholder_lots`); the fetch (base URL, endpoint, page 100,
  budgets 70 and 50, caps 14 and 10, 5 days, 1,000 and 500 reports, 800 attempts, one repeat, one refetch, seed
  prefix `d003:days`, the description text type, D002's User-Agent); C1 and C2 as above; the two hand copies; the
  reported switches; the download script and code;
- the report's sentences (P5).

`check.package_hits` on M1's names (D001's, D002's and D003's columns) finds nothing in the onboard package.

## The workflow

`.github/workflows/onboard-d003.yml`: started by a push that changes `docs/collective/onboard/run-003.json` (any
branch but `main`), or by hand (E14). `contents: read`, no persisted credentials, Python 3.11, 180 minutes, and the
same pinned action commits as `onboard-run.yml`. The steps:

1. the offline tests: `python -m unittest tests.mycelic.test_collective_onboard tests.onboard.test_onboard_fetch
   tests.onboard.test_onboard_d003 tests.onboard.test_d002_reproducible
   tests.mycelic.test_collective_guards.OnboardGenericTests`;
2. the settings check, reading `run-003.json` only: its experiment must be `D003` (checked first); the settings
   file's sha256 must equal the one it names; the two must name the same experiment, a plain id. It writes `SETTINGS`
   and `EXPERIMENT` to the job's environment; a refused file sets nothing and fails before any request;
3. the fetch (`a failure here is not a run`), with no `if:`;
4. `echo "=== $EXPERIMENT RUN-START ==="`;
5. to 7. score `maude`, the report (no `--audit`), collect and upload `onboard-D003`, each with
   `if: ${{ !cancelled() && steps.start.outcome == 'success' }}`. The upload holds `report.json`, `report.md`,
   `arm.json` and the drafted packs that passed their check; a withheld report, or none, uploads alone. Never the
   exports, the fetch's working files or `fetch.json`.

`onboard-run.yml` is unchanged. A push of `run-003.json` also starts it, and its settings check refuses the newest
run file (`not a D002 run file`) before any download; `D002FrozenTests` runs that check under `bash` to show it.

## Choices the rule left to the build

None changes a value the rule fixes.

- **The order of keys.** "Sorted by `mdr_report_key`": keys made of digits sort by their value; any other key sorts
  after them, as text.
- **Exports of dropped companies.** A company dropped by a minimum has its export written, so the fetch can print its
  bytes and sha256 (E13); it is not listed in `companies.json`, so nothing reads it. A company dropped by the
  exact-name check has no days walked and no export; its line prints `null` bytes.
- **Sizes and hashes in `fetch.json`, not `source.json`.** The score copies `source.json` into `arm.json`, so into the
  report's inputs. The report's backstop withholds a report that prints, as a whole token, a record id of five or more
  characters. A byte count is an integer of a record key's shape, so it is kept out of the report. The sizes and
  hashes are printed by the fetch in the job log, before the marker, and kept in `fetch.json`, which is not uploaded.
- **Missing keys** are `T` minus the distinct keys, never below 0. A refetch whose first page finds the day empty or
  too large leaves the day incomplete, counted under `keys`.
- **Control characters** (exclusion 2) are Unicode category `Cc`.
- **The placeholders** are copied into the settings, and a test checks that they equal `field-coverage.json`'s list.
- **M2's "at least three companies"** is held by two things together: the fetch stops, before the run, unless
  `companies.json` lists at least three used companies; and D002's M2, unchanged, fails unless every listed company
  was drafted and its pack loaded.
- **P8's interval sentence** is a settings sentence (P5), like the others.

## Tests

- **Two tests changed because a D003 file exists.** No assertion about D001 or D002 is weakened.
  - `tests/mycelic/test_collective_guards.py`: `ONBOARD_SETTINGS` now lists `D003-settings.json` beside D001's and
    D002's, and the docstring says why: D003's M1 names all three settings files' columns (P12). The guard then also
    checks that no D003 column name appears in the onboard package. Each D001 and D002 name is still checked.
  - `tests/mycelic/test_collective_packs.py`, `DisclaimerTests.test_official_wording_occurs_only_on_the_allow_listed_lines`:
    the test allows `device_quality`'s disclaimer wording on two lines only, and scans all of `docs/collective/`. The
    hand copies keep `device_quality`'s `pack.json` byte for byte (E1 (c)), so each holds that same line. The
    allow-list now names the two copies' `pack.json` with that exact line; any other occurrence, or any other line,
    still fails. The full `tests/mycelic` run found this; the onboard suites do not read `pack.json`'s wording.
- **Found by running the offline step on the tree the owner will push.** A copy of this tree, committed to a fresh
  git repository with a `run-003.json` naming `D003-settings.json` and its sha256 (`sim_run_tree.py`, outside the
  repository), showed that two of the new tests would have stopped the run at step 1: one asserted that
  `run-003.json` does not exist, and one pinned the whole rule file, whose "Runs" section E14 expects to change.
  They now check, when `run-003.json` exists, that it names D003 and its settings, and pin the rule's sha256 up to
  "Runs" (`f99aa2188e841fc282a7a12405d35bb35d8c87f846e078d57ac297eab61a2c10`). On that tree the offline step then ran
  267 tests, OK, and the settings check accepted the file and wrote `SETTINGS` and `EXPERIMENT`. Outside a git
  repository the D002 test fails, as D002's own report does: M1 needs a code commit, and the runner's checkout has one.
- **New in `tests/onboard/test_onboard_d003.py`** (73 tests), against the local archive:
  - the fetch: the selection counts and order, each exclusion, the companies file, a company under its minimum
    dropped and not replaced, the calls (612, the design maximum without repeats), the User-Agent, the seven columns
    in order, `makers[]` with the dropped name, the key order, only the description text written, the drafter reading
    the export, whole kept days, the source record, nothing but counts printed;
  - the walk: the seeded order, paging that shifts once (refetched and kept) or always (left out), an exact check
    that differs twice (dropped, with its export line), fewer than three companies, the attempt cap by class name,
    a 500 after the connector's retries, 429s retried and counted, malformed records and answers whose distinctive
    values reach neither stdout nor stderr, a refused request, `--dry-run`;
  - the settings, the hand copies, E1's row, P4, E6's controls, E3's clusters, C1 and C2 (margin, superiority, not
    decided by minimum or guard), E7's echoes and repeats, E4's single-narrative terms, P13, P12, P5;
  - a full arm (B 200, 60 per company) through score and report: five criteria, no audit, both criteria from one
    sample, the cluster, space, subset and guard tables, the floor counted by days, no name, key, lot, UDI, patient
    value or day in any output or uploaded pack;
  - the report's guard: a maker's name, a word of one, a record key or a kept day planted in a printed string
    withholds the report; a clean arm clears; patient, report-number, lot and UDI values never reach the export;
  - the workflow (triggers, permissions, pins, step order, upload, the settings check under `bash`) and the frozen
    files (D001's and D002's settings, `run-002.json`, a `run-003.json` only as a D003 run file, the rule up to
    "Runs"), and `onboard-run.yml` refusing a `run-003.json` under `bash`.

Counts from `python -B -m pytest <path> -q -p no:cacheprovider` on the tree of the commit holding this note:

| Tests | Result |
|---|---|
| `tests/onboard` | 189 passed, 97 subtests passed |
| `tests/onboard/test_onboard_d003.py` | 73 passed, 41 subtests passed |
| `tests/mycelic/test_collective_onboard.py` | 164 passed, 96 subtests passed |
| `tests/mycelic/test_collective_guards.py` | 83 passed, 732 subtests passed |
| `tests/mycelic/test_collective_packs.py` | 64 passed, 29,445 subtests passed |
| `tests/mycelic/test_collective_x3.py` (the hash pins) | 32 passed, 141 subtests passed |
| `tests/market`, `tests/routing_spike` and the three lab test files that read `docs/collective/` | 310 passed, 16,906 subtests passed |
| `tests/mycelic` | 1,710 passed, 1 xfailed, 45,604 subtests passed |
| `onboard-d003.yml`'s offline command (`python -m unittest ...`) | 267 tests, OK |
| `onboard-run.yml`'s offline command | 189 tests, OK |

## The dry run

**The archive.** `tests/onboard/openfda_fake.py` serves an invented device-event archive on `127.0.0.1`, answering
the three query shapes the fetch sends (a count list, an exact-name check, a day's page). Every maker name, report
key, narrative, lot, UDI, report number and patient value in it is invented; the problem names are FDA terms that
this repository's maps already name. It holds six invented makers and five more names in the count lists, one for
each exclusion: `UNKNOWN` (a placeholder), `12345` (no letter), a name holding a double quote, a variant spelling of
the largest maker, and a name with too few reports. Each maker files a seeded number of reports a day, with some days
too large for the cap, some empty, some reports naming a second maker or a blank one, about one narrative in ten
echoing its filed label, some quoting an FDA generic term, and some templated.

**The steps.** `dry_run_d003.py` (outside the repository) ran the workflow's steps after the settings check, with
`D003-settings.json` unchanged (B 10,000, 200 per company), on one machine with 4 cores. It was run on `445b738`;
an earlier run on the tree before that commit gave the same exports, `arm.json` and report, except the code commit
and hash.

| Step | Exit | Seconds |
|---|---|---|
| fetch, select and split | 0 | 2.5 |
| `=== D003 RUN-START ===` | printed after the fetch | |
| score `maude` (4 companies, two hand copies) | 0 | 262.6 |
| report (no audit file) | 0 | 1.0 |

The largest process used 72 MB.

**The fetch.**

- The count lists held 11 names each, all in both. Excluded: 2 placeholders, 1 not searchable, 1 variant, 1 with too
  few reports. 6 qualified and 5 were chosen. Every exact-name check passed on its first try.
- 612 calls and 612 HTTP attempts: the two count lists, then per company 2 checks and 70 + 50 pages (E9's bound
  without repeats).
- Per window, each company kept 59 to 67 of its 70 walked training days and 41 to 47 of its 50 test days. No day was
  incomplete or refetched, and no key was duplicated or missing. Six days were too large, all over the per-day cap
  and none over the budget left.
- `d5` kept 920 training reports with a narrative, under 1,000: it was not used (`report_minimum`), and its export
  was written and printed. 4 companies were used.
- The exports: 5 files, 14,200 reports, 13,475 with a narrative, each object holding E5's seven keys in order (six
  where there is no narrative).

**The score and report.** The verdict was **pass**: C1, C2, M1, M2 and M3 passed, and the report read no audit file.

- C1: the best control was `label_words`. Drafted minus it: 0.538, interval over 178 (company, day) clusters 0.508
  to 0.570 (records: 0.507 to 0.570).
- C2, in the space the drafted names reach under the 22-name map: 800 records scored, 600 with a nonempty gold.
  Drafted minus `device_quality` 0.087 (0.071 to 0.105), minus `all_reached` 0.659 (0.644 to 0.674), minus
  `most_frequent_reached` 0.643 (0.603 to 0.683). Under the six-name map: 480 with a gold, reported only.
- The reader guard counted 0 rejected rows, 0 records without a primary entity, 0 unresolved values and 0
  `no_entity` drops, for the drafted reader and both copies.
- Reported shares of the 800 sampled records: 0.083 hold their own filed label, 0.399 a word of it, 0.010 a
  digit-masked repeat.
- M1: no column name in the package, 2,124 lines of download code. M2: 4 drafted, 4 loaded. M3: 4 packs passed the
  floor counted by days, the last guard read 325 printed strings and found nothing, and the backstop held 12,587
  record ids and 388 days and found none in the report. P13 left 8 strings out of each n-gram scan; 0 hits lay
  within a value-map spelling.

**What left the run.** A scan (outside the repository) of the 53 files the steps wrote or printed (stdout and stderr
of each step, `arm.json`, `report.json`, `report.md` and every drafted pack file) looked for every invented maker name
(the second maker's too) and each distinctive word of five letters or more of one; the lot, UDI, report-number,
model, patient and brand values; the manufacturer's other narrative; and all 14,200 report keys. It found none. The
472 kept days appear only in the fetch's own output, as E11 requires.

The numbers say nothing about reading: the invented narratives hold their category's cue words by construction.

## Not done

- **The real run.** This machine made no request to openFDA. The owner adds `docs/collective/onboard/run-003.json`
  after review, naming `docs/collective/onboard/D003-settings.json` and its sha256 above.
- **Linting the workflow.** `actionlint` and `shellcheck` are not on this machine. The workflow parses as YAML, and
  `bash -n` passes on each of its seven `run:` blocks.
- **What the archive cannot show.** The invented narratives hold their category's cue words by construction, so the
  dry run's metrics say nothing about reading. How openFDA pages, how often days are incomplete, and how many names
  each exclusion removes are unknown until the fetch prints its counts.

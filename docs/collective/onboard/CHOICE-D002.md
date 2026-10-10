# Drafting test D002: D001 with quoted fields read, and a later run start

D002 is D001's rule with two changes and nothing else. D001's rule is `CHOICE-D001.md` with both of its sections
"Amended before any run". Every other rule, value, criterion, threshold, window, role and seed of D001 stands as
written there.

The two changes:

- **(a) Rule 1.1, splitting.** Every delimited file is read line by line with Python's `csv` module, so a field in
  double quotes loses its quotes.
- **(b) Rule 9, what counts as a run.** The run starts after both splits, not after both downloads.

This file is committed on 2026-10-10, alone, before any of D002's code. No D002 run has happened, and no D002 run file
exists. How it is built is in `BUILD-D002.md`.

## Why: D001's run 1 and the date probe

- **D001's run 1** ([run 38017322304](https://github.com/anovruzov/NeuralGraph/actions/runs/38017322304), run file
  `run-001.json`) passed its tests, its settings check and both downloads. Then `fetch_msha.py split` stopped with
  `SplitError: no date format parses enough of the date column`. No arm finished, and nothing was drafted.
- **By D001's rule 9, that crash is D001's result.** `CHOICE-D001.md` records it under "Runs" (commit `7b41e1b`).
  D002 does not change it.
- **A schema-only probe then showed why** (`tools/onboard/date_probe.py`,
  [run 38017465175](https://github.com/anovruzov/NeuralGraph/actions/runs/38017465175)). It downloaded the same two
  files and recorded value shapes only: each digit written `9`, each letter `A` or `a`, every other character kept.
  - Every one of the 275,220 `ACCIDENT_DT` values of `Accidents.txt` has the shape `"99/99/9999"`, with the double
    quotes.
  - The definition file gives the date format as mm/dd/yyyy.
- **So MSHA's pipe-delimited file wraps its values in double quotes.** D001's rule 1.1 split pipe and tab files on
  the delimiter with no quoting, and `fetch_msha.py split` did the same. Every MSHA value kept its quotes, and none of
  rule 1.3's formats parses a date in quotes.
- **The header was not quoted where it mattered.** In run 1 the split found every declared column's name in the
  header, split without quoting, before it read any date. So those names carry no quotes.
- **What has been seen:** the shapes and the definition lines above, and run 1's log (sizes, hashes, an error and a
  report of zeros). No record value, no category and no narrative of either source.

## Change (a): rule 1.1, splitting

- **Was:** "Pipe and tab: one row per line, split on the delimiter, no quoting. Comma: Python's `csv` module with its
  default quoting. A row with a different number of fields than the header is rejected and counted."
- **Now:** for every delimiter (pipe, tab and comma), each line is one row. The header is a line too.
  - Each line is read on its own by Python's `csv` module, with that delimiter and the module's default quoting. The
    quote character is `"`. Two quotes inside a quoted field stand for one quote.
  - So a field wrapped in double quotes loses its quotes, and a delimiter inside the quotes does not split it.
  - A quote opens a quoted field only as the field's first character, as the module's default has it.
  - A quote that a line leaves open closes at the end of that line. It never joins the next line to the row.
  - Lines end at `\r\n`, `\r` or `\n`, as D001's build reads them.
  - The module's limit on the length of one field (131,072 characters by default) does not apply. A field can be as
    long as its line.
  - A row whose number of fields differs from the header's is rejected and counted, as before.
  - Every value is then stripped, and an empty value is absent, as before. A field written `""` is empty.
- **The MSHA split** (`fetch_msha.py split`) reads each line by the same rule, with the same function as the drafter.
  Its company rule then sees dates without quotes. Each company file still holds the original lines, unchanged (rule
  2.1), and the drafter reads them by this rule.
- **Dates are unchanged** (rule 1.3). Without its quotes, a value of the shape `99/99/9999` fits `M/D/YYYY`, the
  fourth format, which takes one or two digits for the month and the day. If fewer than 95% of the values parse,
  the draft fails, as before. The split checks the same share.

### Comma files

- **D001** read a comma file with the `csv` module over the whole text. A quoted field could hold a line break, and
  one row could span several lines.
- **D002** reads every file line by line. A comma file reads differently only where a quoted field holds a line
  break, or where a field is longer than the module's limit (D001's reader stopped with an error there).
- **The NHTSA arm's exports hold neither break.** `fetch_nhtsa.py` writes them one complaint per line, from a file
  it reads line by line, with each field stripped. So no field holds a line break, and they read the same under both
  rules. Every existing test of a comma file gives the same rows under both.
- **Why one rule for all three:** one function reads every file, and a quote left open can never take in the lines
  after it.

### What change (a) risks

- **The probe saw only the date column's shape.** It did not show how MSHA writes a double quote inside a narrative.
  - If MSHA doubles it, the module reads it as one quote.
  - If not, the module drops that quote and reads the rest of the field as unquoted text. A pipe later in that field
    then splits it, and the row has too many fields. Such a row is rejected and counted.
- The split prints its rejected count before the run starts. No row is ever repaired.

## Change (b): rule 9, what counts as a run

- **Was:** "the run starts when both downloads have finished. A failure before that (an HTTP error, a timeout, a
  truncated or unreadable archive) is not a run, and the same run file may be pushed again unchanged. From that point
  on, whatever happens is the run's result, a crash or a timeout included." And: "A job stopped by its limit after the
  downloads is a failed run."
- **Now:** the run starts when both downloads and both splits have finished, and both splits have printed their
  counts.
  - A failure before that is not a run: an HTTP error, a timeout, a truncated or unreadable archive, or a split that
    stops with an error, as D001's MSHA split did. The same run file may then be pushed again unchanged.
  - From that point on, whatever happens is the run's result, a crash or a timeout included.
  - A job stopped by its limit after the run has started is a failed run.
- **What a split does before the run starts.** It computes no score. No choice and no count it makes depends on a
  category value or on a narrative's words.
  - MSHA's company rule reads the controller, the mine, the date and whether a narrative is there. It never reads the
    category column's values.
  - The NHTSA split copies each complaint's filed component names into its make's export, as rule 2.2 says. It counts
    none of them.
  - Each split prints counts only.
- **The marker.** The workflow prints `=== D002 RUN-START ===` after both splits.
- **Unchanged:** "a change before any run is an amendment, recorded here as such. Any change after a run has started
  is a new choice file." For D002, "here" means this file.

**Why.** In D001 the run started before the file was read at all. Run 1 then ended on a file it could not read,
before any pack was drafted, so it says nothing about drafting or reading. The splits read only what decides the
companies, and print counts. A failure there shows that the file was not read as the rule expects. It shows nothing
about how any reader scores.

### What change (b) risks

- Anyone who sees a split's counts before the run starts sees: the rows, the rejected rows, the controllers, the
  companies used, each company's rows with a narrative in each window, its training mines, the date format, and each
  make's kept and dropped complaints. No category, label, narrative, term or score.
- If a split fails and the rule is changed before any run, that change is an amendment recorded in this file. Its
  author will have seen those counts.

## The settings and the run

- **The settings:** `docs/collective/onboard/D002-settings.json`. Its values are those of `D001-settings.json`,
  unchanged, except `experiment`, which is `D002`. So D002 keeps D001's seeds (`d001:boot`, `d001:sample`,
  `d001:permute`), its `kind`, and its `choice` field, which names `CHOICE-D001.md`, where every value is fixed.
- **The experiment id comes from the settings** in every printed artifact: the report's title, `report.json`'s
  `experiment`, the printed block markers (`=== D002 report.json BEGIN ...`) and the run start marker.
- **The trigger:** the owner adds `docs/collective/onboard/run-002.json` after review:

  ```json
  {"experiment": "D002", "settings": "docs/collective/onboard/D002-settings.json", "settings_sha256": "<64 hex>"}
  ```

  Pushing it starts `.github/workflows/onboard-run.yml`. The workflow runs the newest run file. It refuses that file
  when its experiment differs from its settings file's, or when the settings file's sha256 differs from the one it
  names.
- **The first run is the result.**
- **D001's result stands** as recorded in `CHOICE-D001.md`. `CHOICE-D001.md`, `D001-settings.json` and
  `run-001.json` do not change.

## The declaration, added

- D002 was written by the AI system that wrote D001 and this repository's code, after D001's run 1 and the date
  probe.
- Since D001's declaration, it has seen run 1's log and the probe's record, as listed under "Why" above. No record
  value, category or narrative of either source.
- Everything else in D001's declaration stands.

## Runs

None yet.

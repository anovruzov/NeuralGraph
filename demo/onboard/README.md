# The pack drafter on public data

Point D002's pipeline at MSHA's public file of mine accidents, a field no pack covers, for one mine operator. After
the download it reads the operator's export, drafts a pack from its training years alone, reads the later years it
never saw, and counts patterns across the operator's mines while only weekly cells leave each mine's site, under a mine
label. Every number it shows is computed by the run. The figures D002 recorded are read from its choice file and from
`d002-input.json`, and shown beside them.

D002 splits the file by controller (`CONTROLLER_ID`, a parent company that can run several operators and mines) and
calls each one an operator, c1 to c5. This README does the same.

This is the demo option "The pack drafter on public data, live" of `docs/strategy/YC-BRIEF.md`, section 5. D002 is
`docs/collective/onboard/CHOICE-D002.md` (run 38024536763, passed all six criteria).

## The recorded run (c1)

Run [38036725404](https://github.com/anovruzov/NeuralGraph/actions/runs/38036725404), 2026-10-10, commit `a34031b`,
started by `record-001.json`. Its `demo.json` is committed as `recorded/record-001-demo.json` (sha256
`3e9f2e790f703d22f0cd2de307659cd517f35655f076f2d3eafd1ddc5cac1f00`, the hash the run printed beside the file in its
log); `render_console` on it gives the console text the log holds, line for line. Every figure below is from that
file.

- **Input:** `Accidents.zip`, 52,269,752 bytes, sha256 `62d0c861a5c3…`: the file D002 read. The job took 1 min 44 s
  on a shared runner: 66 s of tests, 1 s of download, 29 s for the demo. The demo's own steps took 28.5 s: (a) 9.9 s,
  (b) 1.5 s, (c) 7.3 s, (d) 6.7 s, the guard 3.1 s.
- **(a) Read:** 275,220 rows, 0 rejected, 57 columns. 6,675 controllers; 28 qualify by D002's rule and 5 are used.
  c1's export: 5,276 rows; 1,780 training rows and 1,033 held-out rows with a narrative, from 13 mines.
- **(b) Drafted:** from the training years alone (2015 to 2021), 14 filed categories passed the floor, 5 became
  predicates, with 81 terms. The privacy floor passed. The refusal removed 2 terms; 0 terms were withheld from the
  screen. Among the terms: `fell`, `ankle`, `stairs`, `tripped` (slip or fall of person); `hammer`, `knife`,
  `pry bar` (hand tools); `pinched`, `index finger` (handling of materials); `haul road`, `pothole` (powered haulage).
- **(c) Read the later years (2022 to 2024)**, 200 records drawn from 863: drafted pack micro F1 0.568
  [0.500, 0.632]; majority prior 0.360 [0.295, 0.425]; permuted labels 0.000; label names 0.020 [0.000, 0.049].
  Drafted minus the best control: 0.208 [0.108, 0.304]. This reproduces D002's figures for c1 to three places.
- **(d) Counted across c1's mines:** 1,033 records at 10 mines over 139 weeks, as D002's M4 recorded. 822,676 bytes
  left the mines, under the labels m01 to m10, and the guard found no record id, mine id or refused value in them.
  From the weekly cells alone, X raised 1 alert and S none. The review list holds 4 items: 1 from the weekly cells
  (slip or fall of person, X in week 2023-W51) and 3 that R_mf alone raised, which need record-level codes counted
  centrally and are shown as reference only.
- **Guard:** 26,180 record ids, 392 mine ids and 11,770 refused values of the 5 operators were searched for in every
  output. None was found, and nothing was withheld.

What it shows: the same code, given only an export and a settings file, read a field it had never seen and scored
above every control on later years, in under 30 seconds of compute, reproducing a pre-registered result. What it does
not show is under "What it does not show": no early warning is measured and the review list is unjudged.

## Run it

Two commands. Python 3.11 and the standard library; the network is needed for the download only.

```
python tools/onboard/fetch_msha.py download --out work/raw
python demo/onboard/run_demo.py --raw work/raw --out work/demo
```

- `--company c1` picks the operator (c1 to c5, D002's labels and order; c1 by default).
- `--settings FILE` defaults to `docs/collective/onboard/D002-settings.json`. Any other file is said to be not D002's,
  and the demo then compares nothing with D002's run.
- `--work DIR` keeps the split exports, the drafted pack and the controls (record data). Without it they go to a
  temporary directory that is deleted. It can never be `--out` or inside it.
- `--markers` also prints `demo.json` between `=== onboard-demo demo.json BEGIN lines=<n> sha256=<hex> ===` and
  `... END ===`.

It prints the console version on stdout and one progress line per step on stderr, and writes `demo.json` and
`demo.html` to `--out`. The page is self-contained (no script, no link), follows the system's light or dark setting
and fits a phone. Exit codes: 0 shown; 1 withheld by the guard, or the drafted pack failed its privacy floor; 2 a usage,
input or run error.

**To record it on a runner:** start the `onboard-demo` workflow by hand (operator c1 unless the input names another),
or push `demo/onboard/record-<n>.json` holding `{"company": "c1"}` (on a push it takes the file with the highest
number, in version order: `record-10.json` after `record-9.json`). It runs the demo's tests, downloads, runs the
demo, prints `demo.json` between the markers and uploads `demo.html` and `demo.json` only. A request file holds
the operator label and nothing else (a test checks this); the first, `record-001.json`, asks for c1.

## What each step shows

Each step shows its wall time.

The header says what is set up for this field: the settings file names this source's columns (the columns it
expects, the five roles, the refused columns and the column that splits the file), and `fetch_msha.py` (its line
count is printed) is MSHA's download and split code. The drafter's code (the onboard package) names no column. The
header also gives the input file's size and sha256, and whether it is the file D002 read
(`demo/onboard/d002-input.json` holds D002's record of it: its size and the first 12 hex digits of its sha256).

- **(a) The export read.** `fetch_msha.py split`, with the settings, cuts MSHA's file into one export per operator,
  c1 to c5 by D002's rule. The demo shows the file's rows, rejected rows, columns and date format; how many
  controllers there are and qualify; the chosen operator's rows and columns; and the roles. The roles are data in the
  settings file. The refused columns (operator, controller and contractor names and ids, and others) are read only to
  split the file (`CONTROLLER_ID`), to refuse and withhold terms, and to check every output, and are never shown.
- **(b) The drafted pack.** `draft.draft_export` over the training years, the loader, and the privacy floor
  (`check.check_pack`). It shows how many filed categories passed the floor, which became predicates, and each
  predicate's label and first terms as D002's report prints them, less any term withheld from the screen (see the
  guard). It says what the refusal removed, in counts, and how many terms were withheld, by reason.
- **(c) Reading the held-out years.** The functions of `onboard/score.py`, in the order D002's per-operator step calls
  them: the same sample (up to 200 records), the same seeds and the same 10,000 bootstrap draws. Each reader sees
  only the narrative and is scored against the filed category. It shows the drafted pack's micro F1 with its interval
  against the three controls (the majority prior, permuted labels, label names), the difference from the best
  control, and D002's recorded figure beside the demo's. The last line says whether they reproduce D002's run, differ
  from it, or cannot be compared (other settings). When they differ with D002's settings, it says whether the input is
  the file D002 read (then the code differs) or not (then MSHA has revised the file since, or it is another file).
- **(d) The cross-mine audit.** D002's M4, with each mine id replaced by its label (m01 onwards, in the order of the
  ids) in the normalised export of the held-out years, before the pilot audit (`pilot.audit.audit`) runs with no
  outcomes, every audit setting at its default. Each mine runs as its own site inside this one process, built from the
  operator's export on the machine that runs the demo; nothing is sent over a network. What left a mine is what crossed
  from its site's store to HQ's: every bundle and cell carries its label, never its id. The same audit then runs under
  the mines' own ids, D002's M4 path, and the demo says whether the two give the same result (alerts, weeks, review
  list and counts); D002's recorded M4 counts are compared with that run, and its own figures are shown when it
  differs. For each mine it shows the weekly bundles and the cells that left: one cell per category, week and channel
  that had a record, holding counts and, with 3 or more records, the lowest match confidence (one of the pack's fixed
  levels). A count under 3 leaves as the string `'<k'`, and each bundle carries k (3). The table counts the cells with
  a count under 3. The bytes that left the mines are scanned by the guard.
  Then the alerts by channel, X and S first: they read only the weekly cells that left the mines, and each alerts only
  when a category's counts rise at 2 or more mines in the same weeks (the pack's cross-site minimum). R_mf, P and PRR
  are reference channels over record-level codes counted centrally. With no outcomes, every X, S and R_mf alert lands
  on the review list, grouped by pattern; P and PRR add nothing to it. The review list is shown in two parts:
  - **from the weekly cells:** each item X or S raised, with the weeks of its X and S alerts, the number of mines that
    sent HQ a cell of it in the 8 weeks up to one of those alerts, and its channels (and whether R_mf also raised it).
    Those mines are counted from the cells the X and S runs read at HQ, and must be the ones the audit took from the
    sites' own stores for the same alerts, or the demo stops with an error;
  - **reference only:** each item R_mf alone raised, apart, with the mines the audit lists for it. It needs
    record-level codes counted centrally, which this setup does not send.
- **(e) What the review list means.** One line: how many items came from the weekly cells (categories whose counts
  rose at 2 or more of the operator's mines in the same weeks, matching no outcome on record because the demo has no
  outcomes file; only the operator's own people could say which are real), and how many are reference only.

The cells per mine, the cells the X and S runs read and the bytes that left are read from the audit's own collective
store and receive log while the labelled audit runs, and the X and S alerts as the audit computed them (`Watch`). The
audit's result does not change, and every function the watch wraps is put back.

## What it does not show

- **No early warning is measured.** The demo has no outcomes, so nothing says whether any alert came early, or at all.
- **The review list is unjudged.** No one has checked whether any item is a real problem.
- **The filed categories are the answer key** of the reading score. They are not checked labels. The reader is the
  lexical extractor, not a model. The interval covers this operator's sampled records only.
- **Public data.** Records written to a regulator, not a company's own files.
- **Not "no configuration".** The settings file names this source's columns, and `fetch_msha.py` is download code
  for it (D002's M1 counts such code). The drafter's code names no column. The screen says so too.
- **Operators are c1 to c5** and mines m01 onwards. The guard (below) looks for every record id, mine id and refused
  value of the split. A drafted term is withheld from the screen when one of its words is a word of a refused value,
  or when the operator's narratives write that word like a name or never in lower case. Case is not read at the start
  of a sentence, after a full stop (so after "Mr." or "Dr.") or in narratives written all in capitals. A name that is
  not a word of a refused value is not caught when the narratives write it in lower case in most of its uses, or in
  lower case even once with its other uses only where case is not read, so the demo does not claim that no name is on
  screen.

## The D002 figures it compares with

From `CHOICE-D002.md`, "Runs", run 38024536763. The demo reads them from that file when it runs.

| Operator | Drafted micro F1 | Majority prior (D002's best control) |
|---|---|---|
| c1 | 0.568 | 0.360 |
| c2 | 0.604 | 0.355 |
| c3 | 0.558 | 0.260 |
| c4 | 0.530 | 0.320 |
| c5 | 0.532 | 0.425 |

- **M4, the audit on c1's drafted pack:** 1,033 records, 10 sites, 139 weeks, a review list of 4. Step (d) compares
  its own counts with these when the operator is c1.
- **Pooled over the five operators** (not computed by the demo): drafted 0.559 [0.532, 0.586] against 0.344 for the
  majority prior.
- D002 recorded each operator's F1 to three places and no per-operator interval, so the demo's intervals have nothing
  to compare with. They use D002's seeds and draws, so on the same file they are the intervals D002's arm file held.
- The demo compares to three places, after rounding to four as D002's report did. With D002's settings, a difference
  means the input file or the code differs from that run's, and the demo says which, from the input's size and
  sha256 against D002's record (`d002-input.json`: 52,269,752 bytes, sha256 `62d0c861a5c3…`, as D002's report.md
  printed it; `CHOICE-D001.md` records the same for D001's download that day). MSHA refreshes the file, so a live run
  will most likely say the file was revised.

## The guard

Before anything is printed or written:

- the strings that came from records (category labels and every first term of each predicate, shown or withheld:
  the only terms the screen can print) go through D002's last guard, against the operator's own export
  (`report.company_hits`);
- a drafted term is withheld from the screen (`withheld_reason`) when one of its words is a word of a refused value
  of any operator in the split (letters only, at least 3 of them: short words and single-word values included, which
  D002's refusal does not cover), or when that word is not a plain word of the operator's narratives
  (`name_shaped_words`). A plain word is written in lower case, and written like a name (a capital first letter, or
  all capitals, away from the start of a sentence) in fewer than half of its uses that show case. Any word after
  '.', '!' or '?' counts as a sentence start, and narratives written all in capitals show no case, so one lower-case
  use makes a word plain when its other uses are only there. So a word written
  like a name, in capitals inside mixed-case text included, and a word never written in lower case (only at sentence
  starts, or only in narratives written all in capitals) are withheld. The screen counts the terms withheld for each
  reason, and the narratives written all in capitals, whose case is not read;
- the whole console text, `demo.json`, `demo.html` and the bytes that left the mines are scanned for every record id
  and mine id of every operator in the split (D002's backstop, `report.Backstop`, values of at least five
  characters), and for every value of a refused column of at least four characters that holds a letter, or of five.
  An export of the split that cannot be read counts as a hit, as in D002's guard.

A hit replaces every output with the hit counts by kind and nothing else, and the exit code is 1. An error text is
shown only when the same checks find nothing in it (and none of its words is refused, a word of a refused value or
not a plain word of the narratives); a step (a) error with no readable export to check it against is never shown. The
drafted pack's loader error is cut to its first part, as D002 cuts it. Any other error prints its class name only,
never a traceback.

## Dry run (synthetic)

On a synthetic file in MSHA's layout (every value invented: every field in double quotes, pipe-delimited, dates
mm/dd/yyyy; 276,568 rows, 2,103 controllers, c1 with 2,313 rows at 5 mines), with D002's settings, on one machine with
4 cores: (a) 4.5 s, (b) 0.4 s, (c) 5.3 s, (d) 5.4 s (the audit runs twice: under the mine labels and under the mines'
own ids), the guard 0.7 s; 16.3 s in all. Every step ran, nothing was withheld, and no drafted term was held back
from the screen. The audit under the labels gave the same result as under the own ids, and the mines behind each
counted item were the same from HQ's cells as from the sites' stores. None of the markers planted in the file's
identifying columns and narratives, and no eight words of any narrative, was in stdout, stderr, `demo.json` or
`demo.html`. Its F1 values mean nothing about MSHA. Step (c) said they differ from D002's run and that the input is
not the file D002 read, as it should. The real file's narratives are longer and its c1 larger (D002's M4: 1,033
held-out records at 10 mines); D002's MSHA arm took 89 s for five operators on a shared runner, scoring and intervals
included.

## Files

- `run_demo.py`: the demo.
- `SCRIPT.md`: a 2-minute talk track that says only what the output shows.
- `.github/workflows/onboard-demo.yml`: the recording workflow.
- `d002-input.json`: D002's record of its input file (size and sha256 prefix).
- `tests/onboard/test_onboard_demo.py`: every step on a synthetic export in MSHA's layout; nothing identifying in any
  output, names planted in tens of narratives included (capitalised, in capitals inside mixed-case text, only in
  narratives written all in capitals, as a common word, and words of the operators' names in lower case); what left
  the mines carries labels only; the mines behind each counted item come from HQ's cells; a reference-only item is
  never shown with the counted ones; the reading numbers equal `score.run_arm`'s and the audit D002's M4 path; the
  page has no script or link. The regular CI (`mycelic.yml`) runs `tests/onboard`, and the recording workflow
  runs the demo's tests before it records.

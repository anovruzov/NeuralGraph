# The pack drafter on public data

Point D002's pipeline at MSHA's public file of mine accidents, a field no pack covers, for one mine operator. In one
command it reads the operator's export, drafts a pack from its training years alone, reads the later years it never
saw, and counts patterns across the operator's mines while only weekly counts leave each mine. Every number it shows
is computed by the run. The figures D002 recorded are read from its choice file and shown beside them.

This is the demo option "The pack drafter on public data, live" of `docs/strategy/YC-BRIEF.md`, section 5. D002 is
`docs/collective/onboard/CHOICE-D002.md` (run 38024536763, passed all six criteria).

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
or push `demo/onboard/record-<n>.json` holding `{"company": "c1"}`. It runs the demo's tests, downloads, runs the
demo, prints `demo.json` between the markers and uploads `demo.html` and `demo.json` only. No request file is
committed here; the owner pushes one to record.

## What each step shows

Each step shows its wall time.

- **(a) The export read.** `fetch_msha.py split`, with the settings, cuts MSHA's file into one export per operator,
  c1 to c5 by D002's rule. The demo shows the file's rows, rejected rows, columns and date format; how many operators
  there are and qualify; the chosen operator's rows and columns; and the roles. The roles are data in the settings
  file. The refused columns (operator, controller and contractor names and ids, and others) are read only to refuse
  words and are never shown.
- **(b) The drafted pack.** `draft.draft_export` over the training years, the loader, and the privacy floor
  (`check.check_pack`). It shows how many filed categories passed the floor, which became predicates, and each
  predicate's label and first terms, exactly as D002's report prints them. It says what the refusal removed, in
  counts.
- **(c) Reading the held-out years.** The functions of `onboard/score.py`, in the order D002's per-operator step calls
  them: the same sample (up to 200 records), the same seeds and the same 10,000 bootstrap draws. Each reader sees
  only the narrative and is scored against the filed category. It shows the drafted pack's micro F1 with its interval
  against the three controls (the majority prior, permuted labels, label names), the difference from the best
  control, and D002's recorded figure beside the demo's. The last line says whether they reproduce D002's run, differ
  from it, or cannot be compared (other settings).
- **(d) The cross-mine audit.** D002's M4: the operator's normalised export of the held-out years and the pilot audit
  (`pilot.audit.audit`) with no outcomes, every audit setting at its default. For each mine (m01 onwards) it shows the
  weekly bundles and the cells that left, and how many cells carried a count under 3 (sent as `'<3'`). Then the alerts
  by channel (X and S read only those weekly cells; R_mf, P and PRR are reference channels over record-level codes) and
  the review list: each item as a category label, a week range, a number of mines and the channels. Never a mine id.
- **(e) What the review list means.** One line: patterns seen at several of the operator's mines that match no outcome
  on record, because the demo has no outcomes file. Only the operator's own people could say which are real.

The cells per mine are counted from the audit's own collective store while the audit runs (`watching_cells`). The
audit's result does not change.

## What it does not show

- **No early warning is measured.** The demo has no outcomes, so nothing says whether any alert came early, or at all.
- **The review list is unjudged.** No one has checked whether any item is a real problem.
- **The filed categories are the answer key** of the reading score. They are not checked labels. The reader is the
  lexical extractor, not a model. The interval covers this operator's sampled records only.
- **Public data.** Records written to a regulator, not a company's own files.
- **Not "no configuration".** The settings file names five columns of this source, and `fetch_msha.py` is download
  code for it (D002's M1 counts such code). The drafter's code names no column.
- **Operators are c1 to c5** and mines m01 onwards. No name, id, narrative or record text is printed or written.

## The D002 figures it reproduces

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
  means the input file or the code differs from that run's (a revised file, for example), and the demo says so.

## The guard

Before anything is printed or written:

- the strings that came from records (category labels and drafted terms) go through D002's last guard, against the
  operator's own export (`report.company_hits`);
- the whole console text, `demo.json` and `demo.html` are scanned for every record id and mine id of every operator in
  the split (D002's backstop, `report.Backstop`), and for every value of a refused column of at least four characters.

A hit replaces every output with the hit counts by kind and nothing else, and the exit code is 1. An error text is
shown only when it holds none of the operator's values.

## Dry run (synthetic)

On a synthetic file in MSHA's layout (every value invented: every field in double quotes, pipe-delimited, dates
mm/dd/yyyy; 276,568 rows, 2,103 operators, c1 with 2,313 rows at 5 mines), with D002's settings, on one machine with
4 cores: (a) 3.5 s, (b) 0.4 s, (c) 5.3 s, (d) 2.5 s, the guard 0.2 s; 11.9 s in all. Every step ran and nothing was
withheld, and none of the markers planted in the file's identifying columns and narratives was in any output. Its F1 values mean nothing about MSHA, and step (c) said they differ from D002's run, as it should. The real
file's narratives are longer and its c1 larger (D002's M4: 1,033 held-out records at 10 mines); D002's MSHA arm took
89 s for five operators on a shared runner, scoring and intervals included.

## Files

- `run_demo.py`: the demo.
- `SCRIPT.md`: a 2-minute talk track that says only what the output shows.
- `.github/workflows/onboard-demo.yml`: the recording workflow.
- `tests/onboard/test_onboard_demo.py`: every step on a synthetic export in MSHA's layout; nothing identifying in any
  output; the reading numbers equal `score.run_arm`'s and the audit D002's M4 path; the page has no script or link.

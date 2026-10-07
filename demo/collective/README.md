# The collective demo (G8)

**A fictional company, synthetic data and a constructed illustration. Internal and YC use only.** Nothing here is a
measurement: the run files say `measurement: false`, and synthetic-fixture numbers never go to a buyer, not even
with a label (STRATEGY sections 9.1 and 12).

## What it shows

Halvern Medical (fictional) runs six plants in four countries. At some of them, complaints about one lot carry only a
generic "malfunction" code; what failed (a battery door coming loose) is written only in the narratives, in English and
German. This is the narrative-only case, constructed on purpose:

- **The alert.** Each plant extracts claims inside its own boundary and sends only k-suppressed weekly counts. HQ's
  detectors (X) flag the lot with no rule written for it. Next to X, every time: **S** (the same detectors over the
  structured codes only, no model) and **R** (the same detectors over the fields allowed to leave, record level, no
  model). The screen says so whenever either baseline catches this case, and lists every related key either one
  flags on the same case. A related key is never called a miss.
- **The check (pushdown verification).** HQ asks the plants one narrow question. Each plant answers from its own
  records with a verdict, count buckets and a reference only it can resolve; a sibling plant that holds the same lot
  without the failure refutes. The commit gate decides the status. The text-overlap scan and the canary scan of
  everything that crossed are shown with it.
- **An extended internal beat** (not in the cut): the supported conclusion becomes a read-only evidence packet
  assembled inside each plant and a CAPA draft for a named owner, each approved and run once. It is built ahead of X4
  and not measured.

## Modes

```
python demo/collective/collective_demo.py --record [DIR]          # headless; writes the six run files
python demo/collective/collective_demo.py --serve [--out DIR]     # live console at http://127.0.0.1:8765/
python demo/collective/collective_demo.py --replay [DIR]          # the committed run, no engine, port 8766
python demo/collective/collective_demo.py --export page.html      # a standalone page of the committed run
python demo/collective/lint_numbers.py demo/collective/recorded/<run-id>
```

**Live vs recorded.** `--serve` runs the engine now; the presenter presses "check with sites" and approves each
follow-up as the named owner, and the screen says LIVE. `--record` runs the same engine headless with scripted
approvals, and the screen says RECORDED. `--replay` and `--export` show a recorded run and run nothing. Without
`--routing`, every plant's model is a deterministic stand-in (no model) and the screen says so; with a routing file
the plants are simulated in one process with one shared model, which the screen also says (RUNBOOK section 16).

## The run-file contract

A run directory holds `scorecard.json`, `trace.json`, `ledger.jsonl`, `leakage.json`, `approvals.jsonl` (the
primary files) and `screen.json`. `screen.json` is built from the primary files: every value it shows is an item
whose `src` points into a primary file, and `lint_numbers.py` re-resolves each one and fails when a display does not
match, when a static text holds a digit or a number word, when the console could render a digit of its own (an
ordered list, a counter), or when a denylisted phrase or a benchmark figure appears. Every digest in a run file is
32 hex characters; no run file holds a path, a host name, a user name or narrative text, and the run's own scans and
self-check refuse to write one that does.

## Re-recording after a change

The committed run must match the engine and the scenario: a fresh recording reproduces its `content_hash`, which the
tests check. After any change to the engine, the scenario or the code it runs:

1. record a new run: `python demo/collective/collective_demo.py --record demo/collective/recorded/<new-run-id>`
   (the directory's name becomes the run id);
2. delete the old directory under `demo/collective/recorded/` (exactly one run is committed);
3. run the lint on the new directory and run `python -m pytest tests/mycelic/test_collective_demo.py -q`.

## What it does not show

- **No measurement.** The world, the case and the decoys are synthetic and written by the same author as the
  detectors; the stand-in model reads the pack's own sentences perfectly.
- **No X4 and no E2.** Approval-routed follow-up is built ahead of X4 and unmeasured; pushdown with a real small model
  is E2 (RUNBOOK section 13), which has not run.
- **R here is model-free.** It is not STRATEGY's R, which includes a frontier model reading the allowed fields; E2's
  central_allowed condition approximates that R.
- **Text only.** The leakage scans show that planted text did not cross; they say nothing about what counts reveal
  (X5).

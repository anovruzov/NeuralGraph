# The collective demo (G8)

**A fictional company, synthetic data and a constructed illustration. Internal use only.** Nothing here is a
measurement: the run files say `measurement: false`. Synthetic-fixture numbers never go to a buyer, not even with a
label (STRATEGY section 12), and **STRATEGY section 9.1, the rules of the YC demo itself, lists "synthetic-fixture
results of any kind" among what is never on screen.** This run's screen shows such results (the X rank and score, the
S and R rows, the verdicts and the gate status, all on a constructed fixture), so as STRATEGY is written it is not
the YC demo; it rehearses the shape of STRATEGY 9.2's cut on fictional data. Up to audit round 2 these docs labelled
the run "Internal and YC use only" and cited 9.1 as if it were a rule about buyers only.

**A decision for the founder, not for these docs:** keep this run internal (the YC cut then needs the partner data or
the public replay that STRATEGY 9.2 calls for), or change STRATEGY 9.1 to allow a labelled synthetic illustration on
the YC screen. Until that is decided, the footer says "Internal use only".

## What it shows

Halvern Medical (fictional) runs six plants in four countries. At some of them, complaints about one lot carry only a
generic "malfunction" code; what failed (a battery door coming loose) is written only in the narratives, in English and
German. This is the narrative-only case, constructed on purpose:

- **The alert.** Each plant extracts claims inside its own boundary and sends only k-suppressed weekly counts. HQ's
  detectors (X) flag the lot with no rule written for it. Next to X, every time: **S** (the same detectors over the
  structured codes only, no model) and **R** (the same detectors over the fields allowed to leave, record level, no
  model). Each row's rank is for the failure mode the screen names; the S and R rows also name the first other key
  of the case they flagged (the lot or the product under the generic code), with its rank and week. The screen says
  so whenever either baseline catches this case, and lists every related key either one flags on the same case. A
  related key is never called a miss. The references row gives U and each site alone with their ranks, and each
  site alone with its week; when one plant alone, running the same detectors on its own records, flags the lot no
  later than X, the screen says so. In the committed run it does, in X's own week: on this constructed case the
  cross-site view is not earlier than a single plant, so the case shows no collective lift (STRATEGY 6.1's baseline),
  and it is not a hidden pattern in STRATEGY 6.1's sense. Rebuilding the case so that no plant's own baseline fires is
  a scenario decision left open (`docs/collective/INTEGRATION.md`, audit round 2).
- **The check (pushdown verification).** HQ asks the plants one narrow question. Each plant answers from its own
  records with a verdict, count buckets and a reference only it can resolve; a sibling plant that holds the same lot
  without the failure refutes. The commit gate decides the status. The text-overlap scan and the canary scan of
  everything that crossed are shown with it.
- **An extended internal beat** (not in the cut): the supported conclusion becomes a read-only evidence packet
  assembled inside each plant and a CAPA draft for a named owner, each approved and run once. It is built ahead of X4
  and not measured. Since B1 the draft is filled from the conclusion and the plants' packets through the pack's
  template, field by field: the title names the failure mode and the lot, the problem statement adds each confirming
  plant's evidence (its support bucket, codes and co-mentioned ids, all already sent to HQ), the affected lots are the
  conclusion's lot and any lot the packets name, and the containment is left for the named owner to write; the
  scorecard gives every field its source, and the screen says "left for the named owner to write" where the template
  writes nothing of substance.

**Why X sees what S and R do not, here.** The screen says it beside the S and R caption: by construction, X reads this
case perfectly, because the scenario author wrote the hero narratives in the pack's own words and the plants read them
with the deterministic lexical stand-in. Extraction is not tested by this run (that is E1 and N1).

**What this run does not show.** On the committed run R (model-free) flags a key of this case (the product under the
generic code) at the top rank in X's own week, and S flags another (the lot under the same code) a week later; the
screen shows both. So this run does not show a case that the fields allowed to leave miss, which is the case STRATEGY
9.2's cut needs. The G8 recording that the final review read is kept, byte-identical, as superseded evidence
(`docs/collective/evidence/superseded/collective-halvern-g10/`).

**The constructed codes-miss scenario (B1b) exists, and its first attempt does not illustrate the case.**
`scenario_codes_miss.json` is the cast `docs/collective/b1/PREREG.md` fixed before any of its worlds existed (a
fictional Tarnwick Devices, where a new complaint-intake form at five plants writes the generic code on every
complaint). Its first recording, `docs/collective/b1/attempts/attempt-1/`, is judged by the pre-registered rule
(`codes_miss.py`), and its own scorecard says the rule does not hold: X caught the case and the check with the sites
says supported, but R (model-free) and S both flag the hero's lot under the generic code in the case's first week, a
week before X. So no committed run shows a case the fields allowed to leave miss. Every screen of that scenario
carries the two pre-registered sentences:

> Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.

> Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.

and the rule's verdict, whichever way it falls, after the gate: whether X caught the case, whether R and S resolved it
no later than X (with the first key each flagged), how many robustness seeds and grid cells hold, and the strict
reading. Replay it with `python demo/collective/collective_demo.py --replay docs/collective/b1/attempts/attempt-1`.

## Modes

```
python demo/collective/collective_demo.py --record [DIR]          # headless; writes the six run files
python demo/collective/collective_demo.py --serve [--out DIR]     # live console at http://127.0.0.1:8765/
python demo/collective/collective_demo.py --serve --cut           # live, the 60-second cut only
python demo/collective/collective_demo.py --replay [DIR]          # the committed run, no engine, port 8766
python demo/collective/collective_demo.py --export page.html      # a standalone page of the committed run
python demo/collective/lint_numbers.py demo/collective/recorded/<run-id>
```

**Live vs recorded.** `--serve` runs the engine now; the presenter presses "check with sites" and approves each
follow-up as the named owner, and the badge says LIVE. `--serve --cut` walks only the 60-second cut's beats live; its
follow-ups run with scripted approval when the presenter moves on from the check (`approval: recorded`). `--record` runs the same engine headless with scripted
approvals, and the badge says RECORDED. `--replay` and `--export` show a recorded run and run nothing, so their badge
says RECORDED whatever mode the run was made in (`screen.json`'s `presentation`); the footer keeps how it was made
(`mode`: a scripted run, or one driven live in the console). Without
`--routing`, every plant's model is a deterministic stand-in (no model) and the screen says so; with a routing file
to a model the plants are simulated in one process with one shared model, which the screen also says; with one that
sends every plant's calls to a local test server, the screen says that instead and names no shared model (RUNBOOK
section 16).

## The run-file contract

A run directory holds `scorecard.json`, `trace.json`, `ledger.jsonl`, `leakage.json`, `approvals.jsonl` (the
primary files) and `screen.json`. `screen.json` is built from the primary files: every value it shows is an item
whose `src` points into a primary file, and `lint_numbers.py` re-resolves each one and fails when a display does not
match, when a static text holds a digit or a number word, when the console could render a digit of its own (an
ordered list, a counter), or when a denylisted phrase or a benchmark figure appears. Every digest in a run file is
32 hex characters; no run file holds a path, a host name, a user name or narrative text, and the run's own scans and
self-check refuse to write one that does. `ledger.jsonl` holds HQ's own model calls only: a plant's per-call ledger
never leaves it, and its usage appears only as the k-suppressed usage summaries that crossed its Boundary
(`scorecard.json` `ledger.site_usage`).

## Re-recording after a change

The committed run must match the engine and the scenario: a fresh recording reproduces its `content_hash`, which the
tests check. After any change to the engine, the scenario or the code it runs:

1. record a new run: `python demo/collective/collective_demo.py --record demo/collective/recorded/<new-run-id>`
   (the directory's name becomes the run id);
2. delete the old directory under `demo/collective/recorded/` (exactly one run is committed; the committed run is
   `collective-halvern-b1a`);
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

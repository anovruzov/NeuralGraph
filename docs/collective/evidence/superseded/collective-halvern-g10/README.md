# Superseded: the G8 recording of the first demo scenario (collective-halvern-g10)

**Evidence, not a live run.** These six files are the committed recording of `demo/collective/scenario.json` as of
commit b0af5b8, moved here byte-identical by B1 (`git mv`; `tests/mycelic/test_collective_demo.py` pins each file's
sha256). Nothing reads them as a run: the demo's replay, export and tests use the runs under
`demo/collective/recorded/`, and the number lint does not lint this directory as a live run. They keep the
portability and model-name scans of every other file under `docs/collective/`.

## Why it is superseded

- **It shows no case the allowed fields miss.** The final review found that R (model-free), the same detectors over
  the fields allowed to leave, flags a key of the case (the product under the generic malfunction code) in X's own
  week, at the top rank, and that S (the codes-only baseline) flags a key of the case (the lot under the same code) a
  week later. STRATEGY 9.2 names a case that R misses or mis-ranks and that pushdown resolves; this run is not one.
- **The CAPA draft repeated one line and listed no lots.** Its title, problem statement and containment were the
  same sentence, and its affected lots were empty although the conclusion is about a lot.

It is superseded by the B1a run under `demo/collective/recorded/` (`collective-halvern-b1a`: the same scenario
re-recorded with a filled draft and per-field sources), whose own scorecard says what it shows: R (model-free) still
flags a key of the case in X's week, so it shows no case the allowed fields miss either. **B1's constructed codes-miss
scenario exists (B1b), and none of its three pre-registered attempts illustrates the case either**: under the rule
`docs/collective/b1/PREREG.md` fixed in advance, R (model-free) or S flags a key of the case no later than X, on the
main world of the first two attempts and on every robustness seed of the third (`docs/collective/b1/attempts/`, and
`docs/collective/INTEGRATION.md`, B1b).

## Where to look in `scorecard.json`

| Pointer (RFC 6901) | What it holds |
|---|---|
| `/hero/detection/X/detection_week` | X's first alert week on the hero key (the failure mode) |
| `/hero/detection/R_mf/related/0` | R (model-free)'s first alert on another key of the case: its key, rank and week, the same week as X's |
| `/hero/detection/S/related/0` | S's first alert on another key of the case: its key, rank and week, one week after X's |
| `/hero/followup/draft/fields` | the CAPA draft's three string fields, each the same sentence |
| `/hero/followup/draft/lists/0` | the draft's affected lots: an empty list |

Read the values from the file itself; this page repeats none of them.

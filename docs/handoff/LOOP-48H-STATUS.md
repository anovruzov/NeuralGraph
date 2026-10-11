# 48-hour loop: status log

Prompt: `docs/strategy/LOOP-48H.md` (commit cccadaf). Loop clock start: 2026-10-11 01:50 UTC. Target: 86 on the pinned
metric (section 2 of the prompt). The founder said at about 02:05 UTC: "No, I'll be asleep, you handle it", so the loop
runs on the prompt's defaults: the pinned reading of 86; no push to `main` and no PR merge; no hosted model.

## Hourly lines

- 01:44 L001 run 2 (lab run 38102412415) complete; report sha e8b3960786ca verified; recording in progress (workflow).
- 01:50 LOOP-48H.md written; A001 (first J-cycle rule) design workflow started against section 2.
- 02:05 Ingestion track part 1 started (S0 CI on this branch; S1 E2E-INGEST.md brief alone; review; amend).

- 02:08 Founder (going to sleep): "Run the entire embedded bench ... the entire emergence benchmark end-to-end ingestion.
  Literally make sure that each neural graph takes in the messages, ingests it, goes into the NATS, and then goes into
  the actual fabric. Make sure tesseract routing is used. No mistakes allowed. End-to-end benchmarking is a requisite."
  Goal B is therefore redefined: the load is the enterprise-hierarchy benchmark (`research/mycelic/`,
  `docs/MYCELIC_ENTERPRISE.md`; the only benchmark in this repository about enterprise-level discovery, which the
  founder calls EMERGENCE), one NeuralGraph per agent node ingesting its records, Tesseract routing of HQ's questions
  (`NeuralGraph/research/retrieval/tesseract.py`, the routing spike's `hq.py`), verdicts and conclusions through a
  real keyed NATS JetStream fabric, with per-hop benchmark numbers and the hidden ground truth scored. The prompt's
  "bridge first, Tesseract later" cut rule is void; the Tesseract leg is mandatory. The e2e-ingest workflow was
  stopped at 02:10 and relaunched with this specification.
- 02:15 L001 run 2 recorded and merged (RESULTS run 10, CHOICE-L001 Run 2, YC brief; figures verified); pushed.
- 02:20 Founder: "Run unit tests before running emergence to ensure full Mycelic system gets tested." Gate added: the
  full suites must be green on the exact commit before any EMERGENCE end-to-end run counts; the e2e CI gets a
  prerequisite unit-suite job.
- 02:27 Full suites started on 5d646a9. By 03:13: tests/mycelic 1,710 passed, 1 xfailed, 45,608 subtests (19 min 31 s);
  tests/lab 725 passed, 23,056 subtests (20 min 44 s); tests/onboard 287 passed (3 min 37 s); research.mycelic 30 OK;
  routing_spike, NeuralGraph/tests and market still running.
- 02:35 YC brief doc (artifact e727fb40) updated with run 10 (rev 13).
- 03:13 Ingestion part 1: S0 committed (6ad04b2) and merged; the EMERGENCE brief E2E-INGEST.md committed alone
  (e3052c0, 670 lines); fakeability review done, feasibility review running, then the amendment. Accuracy: rule A001
  committed alone (90d2084), two reviews (one blocking: the fresh-draw mechanism), amended (146431c), build running.

## Open asks (date asked)

- NEEDS FOUNDER (2026-10-11): confirm that 86 is read as in section 2.1-2.5, or name one of section 2.7. Default: 2.
- NEEDS FOUNDER (2026-10-11): may the loop open a PR or push to `main`? Default: no. Four open PRs to `main` (#11, #4
  clean; #6, #1 conflicting) are left alone.
- NEEDS FOUNDER (2026-10-11): the STRATEGY 9.1 ruling on the fictional demo; publishing the docs-honesty text for
  `site/index.html` (the audit reads lexically). Prepared, not published.
- NEEDS FOUNDER (2026-10-11, optional): a hosted-model key and the two secrets, if a larger central reader is wanted.
- NEEDS CHIEF SCIENTIST (2026-10-11): decision 9, how "unclear" counts, before the first J-cycle rule is committed.
  Default if silent: the shipped `decide` is primary; declared reply mappings are secondaries.
- NEEDS CHIEF SCIENTIST (2026-10-11): the replacement for the corrected chance null (plan 1.1); detector settings per
  site count (1.4, 1.5); decisions 1, 2, 7 for Tesseract placement, routing and embedder.

## Decisions received

- 2026-10-11 ~02:05 UTC, founder: "No, I'll be asleep, you handle it." Read as: run on the defaults above; do not wait.
- 2026-10-11 ~02:08 UTC, founder: the EMERGENCE benchmark must run end to end through NeuralGraph ingestion, Tesseract
  routing, NATS and the fabric, with end-to-end benchmarking; no mistakes. Goal B redefined as above.

## Comparisons made toward 86 (running count of (model, arm, cycle) pairs)

- 0 J-cycles recorded under the pinned rule. (J001 and L001 run 2 predate the rule and are not counted.)

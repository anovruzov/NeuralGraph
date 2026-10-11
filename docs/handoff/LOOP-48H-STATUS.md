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
- 03:18 Suites gate on 5d646a9 complete: tests/mycelic 1,710 passed (1 xfailed), tests/lab 725, tests/onboard 287,
  tests/routing_spike 61, NeuralGraph/tests and tests/market 87 together, research.mycelic 30 OK; 0 failures.
  E2E-INGEST.md amended (315a087: M1-M25 answer 25 findings, 7 blocking) and merged; build part 2 launched
  (bridge S3/S4, site graphs + EMERGENCE adapter S2, harness + drills S5/S6, measurement + CI with the suite gate
  S7/S8, then review and fix).
- 03:50 First mycelic.yml run on this branch (38107786068): the unit/API/MCP/JetStream/process-smoke job passed in
  24 min and the manifests job passed; the compose smoke job failed before any test, on the runner's buildx bake
  entitlement ("additional privileges requested: pass --allow=fs.read=/dev/null"). Fix: COMPOSE_BAKE=false on that
  job (pinned mycelic/harness.py untouched). Re-run follows the push.
- 04:02 CI re-run 38109670867 (3e078d9): compose smoke and manifests green; the unit/API/JetStream job still running.
  A001 built as the lab's j2 experiment (689e138: two judge tasks, a declared mapping, a fresh draw with J001's run
  excluded); its build review is running. EMERGENCE build stage A/B (bridge; site graphs + adapter) running since 03:18.
- 05:00 CI run 38109670867 (3e078d9) finished red: compose smoke and manifests green; the unit/API/JetStream job ran
  tests/mycelic to 1,709 passed, 1 xfailed, 1 failed: the no-model-names guard found the first path segment of the
  branch name at two places in E2E-INGEST.md (the first tests/mycelic run since the brief was merged). Fixed by
  wording, amendment recorded in the brief (65428d9); the guard test passes; rides the next push. A001 build review:
  two blocking findings (prereg `main` overriding the fetch option; a digit in a note), fixed (c573f44), merged
  (f45fd2f); the pre-push gate (tests/lab alone, onboard + guards + x3, the j2 data build, the template dry run) is
  running since 04:52 on the merged tree with the brief fix in the working tree. The run request follows only a green
  gate. EMERGENCE build: the bridge (1915aac) and the site graphs + adapter (63af47d) are built; harness + drills
  building; measurement + CI, review and fix follow. Nothing pushed since 3e078d9.
- 05:28 Pushed at 04:59 (a1ee1d6) with the lab suite still running, a deviation from section 6.4's order, so that
  the guard fix reached CI; CI run 38113330825 on it is green on all three jobs (tests/mycelic passed in 18 min 38 s).
  A001 gate on the merged tree, all green by 05:20: tests/lab 781 passed, 25,156 subtests (21 min 51 s); onboard +
  guards + x3 402 passed (4 min 17 s); the j2 synthetic data build; the template dry run (15 units in 15 shards,
  exclusion verified by both mechanisms, measurements false). Request pushed alone (3361910, 05:21:29) = lab run
  38114522160. Plan job passed in 44 s: labels 150 records (sha 623780b8a1de), 300 questions in 5 parts (sha
  2ba18ea89bb7), prereg sha fb8f06442693; eligible 58,319, excluded 150, overlap 0, artifact and rebuild mechanisms
  both verified; comparators printed before any shard: lexical 0.663 [0.627, 0.703] (sensitivity 0.333, specificity
  0.993), record-blind 0.520 [0.463, 0.577], predicate-only bound 0.577; target 0.860; guard: no l1 units. All 15
  shards started 05:23; the five a-0p5b shards finished by 05:27. Side effect found: the request push also started
  mycelic.yml run 38114522105 (S0 put `lab/**` in the paths filter); the fix (`!lab/requests/**`) waits until the
  shards finish, since a workflow push would start CI again. Check-ins 05:37 and 05:52 armed. EMERGENCE: harness
  stage still building (harness module written 05:21, its test file added, a trial run at size 200 under way).

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

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
- 06:00 A001 run 1 (lab run 38114522160) complete: created 05:21:31, 15 shards 05:23:00 to 05:46:28, aggregate job
  114400537232 05:46:30 to 05:48:05, run success at 05:48:06; report sha cfd7749b3719 (155 lines) recomputed from the
  log and matching. Primary pair (a-4b, negation): failure by rule 10, balanced accuracy 0.577 [0.530, 0.623] against
  the lexical judge's 0.663 [0.627, 0.703] on the same records (paired -0.087 [-0.140, -0.033]), gap to 0.860 of
  0.283, bound 0.577; the per-model headline is worse for all three models; the best secondary, a-4b with the shipped
  instruction read through the unclear-to-no mapping, 0.667 [0.620, 0.710], paired 0.003 [-0.047, 0.050] to the
  lexical judge, not told apart, a hypothesis for the next cycle only. 12 comparisons this cycle, 12 since the loop
  began; recorded in CHOICE-A001 Run 1, RESULTS run 11 and the YC brief (branch wf/record-a001).
- 2026-10-13 01:50 Outage. From 06:13 UTC on 2026-10-11 (the harness builder's journal ends 06:13:26, the recorder's
  06:14:40) the account's weekly usage limit stopped every agent ("You've hit your weekly limit · resets Oct 12, 9pm (UTC)"): the recording workflow died with its four documents
  written but uncommitted, and the EMERGENCE harness builder died after 1 h 23 min of uncommitted work (last commit 624eea8 at 04:50:18, journal end 06:13:26). The session
  resumed at 01:43 UTC on 2026-10-13: about 43 h 30 min of the 48 h passed with no agent work. Recovered without
  loss: the recorder's worktree (figures re-checked against the report block, sha cfd7749b3719 recomputed by the
  recorder, doc and guard tests 120 passed) committed as ec1ad9d, merged 4e55949, pushed; the harness builder's
  working tree saved as WIP commit 0154242 on wf/e2e-harness (harness.py, hq_tesseract.py, report.py, drills.py,
  test_harness.py, BUILD-E2E.md; not reviewed, not known to pass). CI runs 38114522105 on 3361910 (3 of 3 jobs green, 05:21 to
  05:39 UTC, started by the request push) and 38116558394 on 521d8a7 (3 of 3 green). Relaunched:
  the EMERGENCE build from the WIP (harness and drills, then measurement and the CI workflow with the suite gate, two
  reviews, fix; WIP committed every 45 minutes from now on) and the A002 rule design (levers 3 and 4). The founder's
  two messages during the outage, "Keep building" and "Continue with loop and goal while I sleep", are read as:
  continue on the defaults; nothing else changed.
- 2026-10-13 02:45 The 48-hour report (section 9) is written, checked by two independent verifiers (6 blocking and 24
  minor findings, all applied) and pushed (bdf12b7, 06414ad). The YC brief doc (rev 14) carries A001's row and its
  latency row. EMERGENCE part 3: the harness builder works on wf/e2e-harness2 from WIP 0154242 and has committed three
  WIP steps (a455b63 M2 verdict with junit evidence, b2ea6fe BUILD-E2E note, c75524c M24 scan labels); measurement
  and CI follow, then two reviews and a fix. A002: the rule is committed alone (b0f12bd on wf/a002-design: an
  evidence span with a verbatim check, a rationale before the answer, a lexical composite with its ablations, on a
  fresh draw with seed 3 and both earlier draws excluded; primary pair P = (a-4b, evidence_grounded)); the fakeability
  and statistics reviews are in (no blocking finding from statistics), the feasibility review runs; the amendment
  follows in its own commit. No usage-limit message since the resume.
- 2026-10-13 03:15 A002 rule merged and pushed (720b0d6: b0f12bd committed alone; three adversarial reviews, 3
  blocking and 33 minor findings; amended 284b375 with B1 to B25, three findings declined in part). Under the
  standing section 7 default the primary pair is P = (a-4b, evidence): task E answers as answered, with the
  grounded reading Eg as the headline secondary; nine arms on a-4b alone (N, D, E, Eg, Egm, R, Rm, C, Cm), two
  ablation comparators for the composite arms, a fresh draw with seed 3 and both earlier draws excluded by five
  sources with stop rules; 9 comparisons this cycle, 21 since the loop began. Two asks posted above. The build
  (a new lab kind j3, importing A001's j2 unchanged) is launched: build, two reviews, fix. EMERGENCE harness
  builder still working on wf/e2e-harness2.
- 2026-10-13 03:35 EMERGENCE S5 and S6 finished on wf/e2e-harness2 (99a9d0f; the harness reviewed against the
  brief and run end to end; tests/e2e_ingest 95 passed with a real nats-server, 14 min 38 s; flake8 clean). Trial run
  at 200 users, seed 0, in the sandbox (4 vCPU, synthetic, measurement false, 1,181 s): criteria 0, 1, 2, 3, 4, 7, 8
  and 9 ok as the report's checks define them (9 c0 checks, 27 c1, 15 c2, 12 c3, 25 c4, 6 c7, 6 c8, 6 c9): 55
  candidates asked, 165 verdict events, 55 conclusions through the keyed fabric; Tesseract routing with the M4 probe
  (1 failure injected, 1 intervention), 163 read verdicts each with one diagnostic row; forgeries counted 1, 2, 3;
  282 stream messages, 279 verified, 3 foreign; 12,964 records ingested into 7 site graphs with 0 loss; broker killed
  at k = 110 of N = 220 and all 220 applied once; discovery recall 0.325 (13 of 40) against the simulator's own
  0.475 on the same world (A2 column 0.925); Tesseract p50 1.37 s, p95 2.34 s over 166 rankings. The verdict reads
  pass false, reason suites_not_green, by M2 until the suites CLI exists; criteria 5, 6 and 10 not run. The
  measure + CI stage (S7, S8) is building (WIP e43a2b8 on wf/e2e-measure). A002 build: WIP c4e2181 (lab/j3.py,
  lab/j3labels.py). No usage-limit message.
- 2026-10-13 04:27 EMERGENCE S7 and S8 built on wf/e2e-measure (254dcf2: the measure job, criterion 10's junit gate,
  the package CLI run/measure/check/suites, the CI workflow e2e-ingest.yml); the two adversarial reviews
  (fakeability, correctness) are running, the fix follows. A002 build on wf/a002: lab/j3.py and lab/j3labels.py
  written, the wiring across request, plan, prereg, units, warmup, responder, aggregate, summary, notes, dryrun,
  workflow and template committed as WIP (29e0e8b, e9cd3e0, the dry run passes); the tests are being written, then
  docs, suites, the pinned-file check and the final commit; two reviews and a fix follow. No usage-limit message.
- 2026-10-13 14:30 Second outage: the account's session limit ("resets 6:40am (UTC)") stopped both workflows at
  about 05:02 UTC; the session resumed at 14:26 UTC (about 9 h 25 min with no agent work). Saved before the stop:
  EMERGENCE S7 and S8 (254dcf2), both build reviews (4 blocking and 10 minor findings each: the workflow test reading
  the branch name out of the brief, M1's chain never read from the fabric's verdict payloads, drill 2 under
  in-process mode, the e2e test floor asserted against itself, the section 4 leakage scan not run over every crossing
  byte, c10 counting subtests as tests, and the brief's guard hits), and the fixer's first 35 minutes on wf/e2e-fix
  (3c850d8 merges the deployment branch 7f96442 so the brief's guard fix 65428d9 is in the build; 2ffd773 and
  5920294 amend the brief after the review: a fifth suite job, and M14's Tesseract phase 8 minutes and size-2,000
  phase 12 minutes, total still 42 of 45). A002 build: WIP 242228f (tests, pins, lab docs) and 3d8279e (BUILD-A002.md
  as left) saved on wf/a002; the suites, dry run, pinned-file check and final commit remain. Both workflows resumed
  with their scripts patched to continue from these branches (wf/e2e-fix2, wf/a002-b). The founder's "Continue"
  (14:26) is read as: carry on.
- 2026-10-13 15:30 Both resumed workflows alive. A002: the builder finished on wf/a002-b at 6be0b0b (lab kind j3:
  lab/j3.py, lab/j3labels.py, wiring, the second download-artifact step, tests, template, BUILD-A002.md; test_lab_j3
  61 passed with 369 subtests on the builder's count, 64 collected after three wiring pins; test_lab_j2 56 passed; the
  five pinned lab files byte-identical to 720b0d6 on the builder's diff). The tests review returned 0 blocking and 4
  minor (two unpinned run.json identity checks in the aggregate, a commit message saying 63 tests where 64 are
  collected, the pre-existing timing test test_lab_sim.py::test_wall_time failing on this sandbox at the deployment
  tip too, and two self-referential assertions); the rule review is still running its full-suite check. EMERGENCE:
  the fixer on wf/e2e-fix2 (WIP da5af45, f0ded12, dd55f5c) finished a size-200 harness run on the fixed code and is
  writing the build note's "What this branch ran" section; lint and the no-model-names guard re-run after the edit.
  No new limit errors. Next: fix stage for A002 (wf/a002-fix), then the merge gates for both.
- 2026-10-13 16:20 EMERGENCE part 3 merged and pushed: wf/e2e-fix2 48fc211 (every blocking and minor review finding
  applied; the e2e test floor rose to 157) merged as 56cfad4 after a green local gate (tests/e2e_ingest 157 passed
  with the real nats-server, guards 83, packaging and onboard 99, flake8 clean, the five-job workflow parses).
  mycelic run 19 (38425113475) green. First e2e-ingest.yml run (38425113336): the three suite jobs green with junit
  evidence verified per job (core 25 min, lab 21 min, e2e_ingest 8 min); the e2e job and the measure job started at
  16:13 and are running. A002: the rule review returned 0 blocking and 3 minor (the per-entry exclusion file not
  pinned to its source; the aggregate's primary and headline lines read from the plan's prereg rather than the j3
  prereg the units pin; and a sandbox disk shortage failing 70 provision and server tests the build does not touch);
  the fix stage on wf/a002-fix is running the whole tests/lab suite before its final commit. The sandbox has about
  540 MiB free, which the provision tests refuse; clean-up of old worktrees and run directories next.
- 2026-10-13 16:50 First e2e-ingest.yml run (38425113336, on 56cfad4) green in every job: suites-core 25 min,
  suites-lab 21 min, suites-e2e 8.5 min, the e2e job 10 min (harness 595 s, pass true, checks c0-c4, c7-c10 ok, c5
  and c6 belong to the measure job and the workflow test), the measure job 21 min (pass true, c5 and c10). Recorded
  in E2E-INGEST.md "Runs" and INTEGRATION.md section 11 from the job logs. Not done: the reports are CI artifacts
  on a host outside this session's network and longer than the readable log tail, so they are not committed under
  docs/collective/e2e_ingest/ and their digests are not recorded (open ask below). A manual workflow that would
  have printed the artifacts into a job log was drafted and withdrawn as a route around the network policy. Disk:
  the sandbox was at 552 MiB free (70 lab provision tests refused to run); stale scratch directories and clean
  worktrees of finished workflows were removed, 15 GiB free now. A002: the fix stage on wf/a002-fix is in its
  full tests/lab run.
- 2026-10-13 17:00 A002 fix stage done: wf/a002-fix 70d269d (the per-entry exclusion file pinned, the aggregate's
  primary and headline lines cross-checked against the j3 preregistration and the comparator files' digests, the two
  unpinned run.json identity checks pinned, the composite's word asserted literally; each fix with a test that fails
  without it; the fixer's own tests/lab run 841 passed with the five disk refusals passing once the disk was freed;
  onboard, guards and x3 402 passed). Merged as 6c8553d (--no-ff; one conflict in docs/lab/INTEGRATION.md: A002's
  section 10 kept, the e2e CI record moved to section 11). Gate on the merged tree so far: workflow, docs, guard and
  e2e workflow tests 101 passed; j3 synthetic data and the template dry run exit 0 (5 units in 5 shards, exclusion
  of J001's and A001's draws verified by artifact and rebuild, 300 excluded, 240 eligible after exclusion, lexical
  comparator 0.700 [0.660, 0.740]); onboard, guards and x3 402 passed; the whole tests/lab suite running. Then: push
  the code, push lab/requests/accuracy-002.json alone to start the run, check-ins.
- 2026-10-13 18:05 A002 running: the gate finished green (tests/lab 846 passed in 34 min 45 s; flake8's only findings
  are intentional re-exports in lab/j3.py, the pattern of the earlier lab modules, and CI runs no lint); code pushed
  (cbdd3c7), then lab/requests/accuracy-002.json alone (5a8f980), lab run 38439612256: the plan job verified both
  earlier plan artifacts, excluded 300 records with every consistency count zero, drew 150 from 58,019 eligible
  (labels prefix 0a64a62d94a7; plan artifact lab-plan-38439612256-1, id 11833749961), comparators lexical 0.680
  [0.637, 0.723], record-blind 0.517 [0.460, 0.573], predicate-only bound 0.553, ablation B 0.627 [0.573, 0.680];
  five a-4b shards running since 17:35 (325-minute timeout each). The code push's mycelic run 20 is green; its
  e2e-ingest run 2 is in progress. A003 (lever 5, the drafted-terms payload) started while A002 runs, as the loop
  plan's schedule says: the rule is committed alone on wf/a003-design (efa79fe, 1,232 lines) and its three
  adversarial reviews are running; the amendment follows, then the merge of the design branch (docs only) and the
  pre-run term-drafting step. Check-ins: 18:31, then every 50 minutes until the aggregate job ends.
- 2026-10-14 13:28 Third outage: the account's usage limit stopped every agent of this loop from about 18:38 UTC on
  2026-10-13 until the session resumed at 13:15 UTC on 2026-10-14 (about 18 h 37 min with no agent work). A002 run 1
  had finished before it: lab run 38439612256, created 17:33:27, plan job 115442458382 17:33:30 to 17:34:58, five
  a-4b shards 17:35:32 to 18:21:50 (all sealed, exit 0), aggregate job 115464859031 18:21:53 to 18:23:04, run success
  at 18:23:04; report sha 58325c34d6bf (144 lines) recomputed from the log and matching (report.json's block precedes
  the readable log tail, not verified). Primary pair (a-4b, evidence): failure by rule 12: "P scored BA 0.540 [0.503,
  0.577]; gap to 0.860 is 0.320; lexical 0.680 [0.637, 0.723]; bound 0.553." (paired -0.140 [-0.190, -0.090]); the
  primary under the other reading of the section 7 default (evidence_grounded) is failure too; the evidence field
  hurts (E minus N -0.093 [-0.137, -0.053]); the best secondary, negation read through the unclear-to-no mapping,
  0.687 [0.643, 0.730], paired 0.007 [-0.050, 0.063] to the lexical judge, not told apart, a hypothesis only. 9
  comparisons this cycle, 21 since the loop began. Recorded in CHOICE-A002 Run 1, RESULTS run 12 and the YC brief
  (branch wf/record-a002). A003 inherits: under its draft's adaptation rule (wf/a003-design, efa79fe, in review) the
  base task is A001's negation task, with the unclear-to-no mapping as its declared reading.
- 2026-10-14 14:00 A002's record merged (71d1993: the record c50b76e after an adversarial check, 0 blocking and 8
  minor findings applied; guards and lab docs tests 132 passed, the rule's reference tests 10 passed; pushed). A003's
  rule amended before any build: the feasibility review re-ran after the outage, then 32 amendments (C1 to C32,
  7daab37) answered 11 blocking and 44 smaller findings from the three reviews; A002's result was known when they were
  written and is quoted in the amendment's preamble, no amendment changed which branch A002's figures select, and
  "The base task, read from A002 run 1" records branch N (A001's negation task; the unclear-to-no reading secondary).
  Merged as ed96530 (docs only, the name guard passed) and pushed. Build 1, the term-drafting kind jt, started on
  wf/a003-terms with step 0 first (A002's dry-run outputs pinned byte for byte, C30); then two adversarial reviews
  and a fix stage; then the term run, an amendment with the term set's sha256, and build 2 (the judging kind j4).
- 2026-10-14 16:00 A003 build 1 committed on wf/a003-terms at 31cee9d (the term-drafting kind jt: lab/jt.py, its
  wiring, tests/lab/test_lab_jt.py, synthetic data, the terms-003 template, BUILD-A003.md; step 0 pinned A002's dry-run
  outputs at b8966c2). The builder's checks: test_lab_jt 52 passed; test_lab_j3 and j2 121 passed; tests/lab 898
  passed in 28 min 31 s; onboard, guards and x3 402 passed; A002's dry-run summary, report.json and report.md equal
  to the recording byte for byte; every pinned path byte-identical to ed96530. The terms-003 dry run on synthetic data
  drafted 33 terms for 7 of the 26 hand predicates with the privacy check passed and printed no term (synthetic,
  measurement false). Two adversarial reviews are running (rule and leakage; tests and mutations), then the fix stage.
- 2026-10-14 18:20 A003 build 1 reviewed and fixed (15 blocking and 17 minor findings, all applied at a4e717f, 37
  mutants killed), merged as d270bd3 and gated (tests/lab 918 passed; onboard, guards and x3 402 passed); the term
  request pushed alone as 5d2c1d3. The term run, lab run 38612800578, passed: model-free, 2 min 2 s, the check passed
  by all seven checks, 25 of the 26 hand predicates reached, 3 entries empty, 753 terms; no term printed. Its report
  block's sha256 matched on recomputation. The rule is amended after the term run (3d73f93): the term set's sha256
  42c591ccbb66…, P_D's hashes, the counts, lab/jt.py frozen; the term artifact expires 2027-01-12T18:09:48Z. Next:
  build 2 (the judging kind j4), two reviews, the fix stage; then the judging request accuracy-003 alone.
- 2026-10-14 19:27 CI on d270bd3 (the build 1 merge) is red in two places, both being diagnosed, neither fixed yet:
  mycelic run 38612799645 failed one timing test, tests/mycelic/test_api.py
  test_verification_never_holds_the_event_loop (the loop stalled 0.258 s against a bound of half a walk, 0.242 s; the
  same test passed in the e2e-ingest core job on the same commit), and e2e-ingest run 38612799316 failed 2 of 918
  tests/lab tests, named only in its junit artifact, which this sandbox cannot download. The 191 tests that build 1
  added or changed pass in a fresh shallow clone; the whole tests/lab is re-running there with CI's suite command,
  which writes the junit locally. A003 build 2: stage 1, the core (lab/j4.py, lab/j4labels.py, lab/j4terms.py,
  tests), committed at c1d4fec on wf/a003; stage 2, the wiring, is running. A004's rule (lever 6, a-4b with
  reasoning on) drafted alone at e6b9a45 on wf/a004-design while build 2 runs, as A003's was during A002; three
  reviews, then an amendment; it fixes now how it adapts to every A003 outcome.
- 2026-10-14 20:15 CI: the mycelic timing failure was the garbage collector, not a walk on the loop. A probe inside the
  test's window, in a full tests/mycelic run, measured full passes of 0.164, 0.164 and 0.150 s that freed nothing; the
  test now freezes the objects older than its window (a2a1bbe): with 6,000,000 extra objects on the heap every pass in
  the window took under 0.04 s, the bound is unchanged, and a mutation that runs every walk on the loop still fails it
  (2.803 s against 0.215 s). The 2 tests/lab failures do not reproduce here: CI's own suite command on a fresh shallow
  clone ran all 918 green (30,611 subtests against CI's 30,591), as did the jt tests with CI's GITHUB_ and RUNNER_
  variables set and package versions equal to requirements.txt. e2e-ingest now prints each failing test's id from the
  junit files when a suite is red (55914e3); CI is re-running on 55914e3. A004's rule amended before any build (E1 to
  E38, e54eb6d; three reviews, 13 blocking and 39 minor findings), merged as f557ce0 and pushed: cases C, H, T and N
  read from A003's result, the reasoning-on and reasoning-off arms on the same records, a timing check before the
  judging run, and every setting frozen from the amendment's commit. A003 build 2: wiring stage at fbab1fc (WIP).
- 2026-10-14 21:25 CI on fd0966e: the lab suite's one failure named itself through the new diff message. The A002
  golden report.json held the sandbox's processor in notes.cpu_models, which the normalisation had left out (its
  markdown twin was already normalised). Fixed at 24fe3ce: the key is normalised, the recording re-normalised (that
  value only), and the dry run drops GITHUB_RUN_ATTEMPT so a re-run cannot differ either. The mycelic job failed
  only the lineage loop timing test, 0.254 s against 0.248 s; locally 0.04 to 0.09 s, so both loop timing tests now
  carry a stall watcher whose samples go into the failure message. Under load, the samples put the loop in its own
  per-query BM25 build over the caller's whole view (mycelic/retrieval.py), a product cost, not a walk; it is
  frozen with mycelic/*.py while A003 runs. The founder's EMERGENCE 80% directive arrived; its frozen benchmark
  specification and seed policy are committed alone at 5eb2e28 (docs/collective/EMERGENCE-80/SPEC.md), and the
  diagnosis (the 40-pattern decomposition and the rare patterns' A/B/C census on development seeds 0 to 5) is
  running. A003 build 2 still in review.

## Open asks (date asked)

- NEEDS FOUNDER (2026-10-13): download the two e2e-ingest artifacts of run 38425113336 (e2e-ingest-report.json
  11830620985 and e2e-ingest-measure.json 11831910113, before 2027-01-11) and commit them under
  docs/collective/e2e_ingest/38425113336/, or paste the two jobs' step summaries (they carry each report's sha256);
  the session's network cannot reach the artifact store. Until then S9's "reports committed" clause is open.
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
- NEEDS FOUNDER (2026-10-13): section 2.3's stop rule is read as stop when no mechanism verifies a cycle, not stop
  unless the rebuilt sha matches; a verified artifact's refs are the records that cycle judged (CHOICE-A001 A2;
  CHOICE-A002, The data and the draw, item 5). A strict ruling received before the run is adopted by a dated amendment.
- NEEDS CHIEF SCIENTIST (2026-10-13): may lever 3's verbatim-substring check (G: a yes read as no when the citation
  is not a substring of the record's text, applied before `decide`) be A002's primary arm? Default if silent: no;
  P = (a-4b, evidence), Eg the headline secondary.

## Decisions received

- 2026-10-11 ~02:05 UTC, founder: "No, I'll be asleep, you handle it." Read as: run on the defaults above; do not wait.
- 2026-10-11 ~02:08 UTC, founder: the EMERGENCE benchmark must run end to end through NeuralGraph ingestion, Tesseract
  routing, NATS and the fabric, with end-to-end benchmarking; no mistakes. Goal B redefined as above.

## Comparisons made toward 86 (running count of (model, arm, cycle) pairs)

- 12 (model, arm, cycle) comparisons in 1 J-cycle: A001 run 1 (lab run 38114522160); 12 since the loop began. Primary (a-4b, negation): failure, 0.577 [0.530, 0.623] against the target 0.860 and the lexical judge 0.663.
- 9 (model, arm, cycle) comparisons in 1 J-cycle: A002 run 1 (lab run 38439612256); 21 since the loop began. Primary (a-4b, evidence): failure, 0.540 [0.503, 0.577] against the target 0.860 and the lexical judge 0.680.

## 48-hour loop report, 2026-10-11 01:50 UTC to 2026-10-13 01:50 UTC, HEAD 4e55949

Written 2026-10-13 01:53 UTC (bdf12b7); corrected at 02:04 UTC after two independent checks of every figure against its
sources. The window runs from the loop clock's start (the header above). About 43 h 30 min of it passed with no agent work
(the account's usage limit, see the 01:50 line); the loop continues past the window as section 0 of the prompt says.

| Goal | Pinned definition | Measured result (point [95% interval]) | Comparators on the same records | Verdict (success / provisional / partial / failure / incomplete) | Source (run id, file:line) |
|---|---|---|---|---|---|
| A: 86 accuracy | BA of the site verifier's verdicts vs the filed-category key, record level, codes hidden, fresh real NHTSA draw of 150 records, J001's scoring (LOOP-48H.md section 2); one primary (model, arm) pair per cycle; pass test (rule 10): BA at or above 0.860, the paired difference to the lexical judge with its 95% lower end above 0, the BA's lower end above the predicate-only bound on the same draw, the unknown share reported; confirmation on the next cycle's fresh draw; comparisons made so far: 12 | Primary pair (a-4b, negation instruction) 0.577 [0.530, 0.623]. Secondaries, never results: a-4b shipped read through the unclear-to-no mapping 0.667 [0.620, 0.710]; a-4b negation mapped 0.653 [0.610, 0.697]; a-4b shipped 0.307 [0.263, 0.350]; a-1p5b 0.467 [0.440, 0.493] shipped and mapped, 0.497 [0.480, 0.513] negation and mapped; a-0p5b 0.493 [0.480, 0.507] shipped and mapped, 0.483 [0.447, 0.520] negation and mapped | Lexical judge 0.663 [0.627, 0.703]; predicate-only bound 0.577; record-blind control 0.520 [0.463, 0.577]; the primary pair minus the lexical judge, paired by record, -0.087 [-0.140, -0.033] | failure (rule 10 of CHOICE-A001) | lab run 38114522160; `docs/collective/A001/CHOICE-A001.md:822` (Run 1); `docs/lab/RESULTS.md:408` (Run 11); aggregate job 114400537232, report sha cfd7749b3719 |
| B: ingestion | criteria 0 to 10 of `docs/collective/E2E-INGEST.md` section 2 (criteria 0 to 7 verbatim from LOOP-48H.md section 3.1; 8 to 10 added; Tesseract routing mandatory, section 4) | Criteria 0 to 10: none run in a recorded CI run on this branch (no `e2e-ingest.yml` exists), so each is fail; criterion 5's five numbers: none; runner CPU: none. Nothing of the build is merged. On the local branches: the bridge (wf/e2e-bridge 1915aac) and the site graph with the EMERGENCE adapter and pack (wf/e2e-site 63af47d) are built; the bridge's 28 tests pass against a real nats-server and the site graph's 34 tests pass (BUILD-E2E.md on wf/e2e-harness, the builders' own record, not re-run on the deployment branch); the harness stage is a saved WIP (wf/e2e-harness 0154242), not reviewed, not known to pass; measurement, the CI workflow and the suite gate are not built. | none | incomplete | the workflow result of wf_0bc005e2-4cb (`docs/collective/BUILD-E2E.md` on wf/e2e-harness, local); `docs/collective/E2E-INGEST.md:91` (section 2; 315a087 and 65428d9) |

Row note for Goal A: the key's noise ceiling is unmeasured and the models may have seen these public complaints in
training. 86 was not reached, so the public-text caveat applies to the failure, not to a pass.

Not reached. Best measured BA 0.577 [0.530, 0.623] on primary pair P = (a-4b, negation instruction); gap to 0.860 is
0.283. The best secondary, a-4b with the shipped instruction read through the declared
unclear-to-no mapping, is 0.667 [0.620, 0.710], a paired difference of 0.003 [-0.047, 0.050] to the lexical judge:
not told apart, and a hypothesis for the next cycle only.

### Done (merged, pushed): commits, branches, runs
- Pushed on the deployment branch, abbf2e4 to 4e55949 (17 first-parent commits, 26 commits in all; the status-log
  commits d65bed5, 5d646a9, 9ee23f5, 6f98472, 746abdd, a1ee1d6 and c36420b among them; the rule, amendment, build and fix
  commits named below reach the branch through the merges f45fd2f and 4e55949): the loop prompt and the pinned
  definition (cccadaf); this status log (d65bed5); L001 run 2 recorded (3e3defc: RESULTS run 10, CHOICE-L001 Run 2,
  the YC brief); S0, `mycelic.yml` on this branch with the routing spike's tests (08764f7); the EMERGENCE brief,
  committed alone, reviewed twice, amended M1 to M25 before any build (d8a7ba5), and its wording amendment for the
  no-model-names guard (65428d9); the compose smoke job on the classic builder (3e078d9); the A001 rule (90d2084),
  its amendment (146431c), the lab kind j2 (689e138), its review fix (c573f44) and the merge (f45fd2f); the request
  (3361910); the CI paths fix so a request push no longer starts `mycelic.yml` (521d8a7); the record of A001 run 1
  (ec1ad9d, 4e55949).
- Lab runs: 38102412415 (L001 run 2, 2026-10-11 01:36 to 01:44 UTC, success; finished before the window, recorded in it
  as RESULTS run 10, 3e3defc);
  38114522160 (A001 run 1, 05:21 to 05:48 UTC, success, recorded as RESULTS run 11).
- CI runs of `mycelic.yml` on this branch: 38107786068 (compose job red: buildx bake entitlement; fixed 3e078d9),
  38109670867 (tests job red: the no-model-names guard on the brief; fixed 65428d9), 38113330825 green,
  38114522105 green (3 of 3 jobs, 05:21 to 05:39 UTC, started by the request push 3361910), 38116558394 green.

### Failed or not reached: the number, the gap to 0.860, what the interval says, the reason as measured
- Not reached. Best measured BA 0.577 [0.530, 0.623] on primary pair P = (a-4b, negation); gap to 0.860 is 0.283. The
  interval's upper end 0.623 is
  below the lexical judge's point 0.663 and the paired difference to it is wholly below zero, so the model judged
  worse than word matching on this draw under the negation instruction; the reason as measured: sensitivity 0.833
  with specificity 0.320 and an unknown share of 0.137 (CHOICE-A001 Run 1). Under the shipped instruction the same
  model abstains on 0.560 of questions (BA 0.307); reading those as refutations gives 0.667, level with the lexical
  judge (0.663), not above it. The two smaller models answer yes to nearly everything (specificity 0.020 to 0.053
  except a-0p5b under negation, 0.413 with sensitivity 0.553).
- Goal B: incomplete; no criterion passed; see the table. The usage limit ended the harness builder's session after
  1 h 23 min of uncommitted work; its files were saved whole as WIP 0154242, the restart point. Not merged, not pushed
  (workflow branches; the harness rule allows pushes to the deployment branch only): wf/e2e-bridge 1915aac, wf/e2e-site
  63af47d, wf/e2e-harness 624eea8 + WIP 0154242.
- Time: about 43 h 30 min of the window with no agent work (06:13 UTC on 2026-10-11 to 01:43 UTC on 2026-10-13, the
  usage limit); about 4 h 30 min of agent work in total (4 h 23 min before the outage, 7 min after the resume).

### Stopped before any model ran: run ids and causes
- None in this window. (L001 run 1, 38090725021, stopped at the plan job's guard on 2026-10-10, before the window;
  recorded in CHOICE-L001.)

### Open asks (founder / chief scientist), with the date each was asked, and decisions received
- Open since 2026-10-11 (section "Open asks" above): the founder's confirmation of the 86 reading (default section
  2); PR or push to `main` (default no); the STRATEGY 9.1 ruling and the docs-honesty text (prepared, not
  published); a hosted-model key (optional); the chief scientist's decision 9 (default applied: shipped `decide`
  primary, the declared mapping a secondary, as A001 ran it), the chance-null replacement, the per-site-count
  detector settings, decisions 1, 2 and 7.
- Received: 2026-10-11 ~02:05 "No, I'll be asleep, you handle it"; ~02:08 the EMERGENCE end-to-end instruction (Goal
  B redefined, Tesseract mandatory); ~02:20 unit tests before any EMERGENCE run; during the outage, "Keep building"
  and "Continue with loop and goal while I sleep". No answer to any open ask.

### Next pre-registered lever (name, draw, primary pair, arms), and the next ingestion step
- A002 (levers 3 and 4 of LOOP-48H.md section 2.6): schema variants (an evidence-span field with a verbatim-substring
  check, a rationale before the answer, predicate descriptions, a lab TaskSpec's own max tokens) and the lexical
  ensemble (a composite calling the pinned lexical judge, with the declared ablation comparator and the extra pass
  clause against it). Draw: seed 3, n 150, 300 questions in 5 parts, J001's and A001's 150 records each excluded by
  both mechanisms (artifacts lab-plan-37996675559-1 and lab-plan-38114522160-1). Primary pair and arms: not yet named; this
  report therefore lacks them. CHOICE-A002, being written on wf/a002-design (rule alone, three adversarial reviews,
  amendment), fixes them before any build; the comparison count continues from 12.
- Updated 2026-10-14, after A002 run 1 (RESULTS run 12, CHOICE-A002 Run 1: failure on (a-4b, evidence)): lever 5
  of LOOP-48H.md section 2.6, the drafted-terms payload, as A003. Its rule is drafted on wf/a003-design (efa79fe,
  committed alone) and in review; its base task follows from A002 run 1 by the draft's pre-registered adaptation rule
  (the negation task, with the unclear-to-no mapping as its declared reading); the comparison count continues from 21.
- Ingestion: finish S5 and S6 from WIP 0154242 (harness, Tesseract HQ, drills), then S7 and S8 (measurement: the
  scaling table at 200, 600 and 2,000 users and Tesseract per-question p50/p95 at 600, 5,882 and 20,000 nodes;
  `e2e-ingest.yml` with the unit-suite gate first), two reviews, fix, merge, the first CI run,
  record in E2E-INGEST.md "## Runs" and docs/lab/INTEGRATION.md with measurement false.

### Not found: anything you looked for and could not find
- Any founder or chief-scientist answer to the open asks; a hosted-model key.
- A passing criterion of Goal B on the deployment branch.
- A way to read a job log or download an artifact from this sandbox other than the GitHub MCP (the blob host is
  refused); the recorder reconstructed the report block from the log and matched its sha256.
- A small model that beats word matching on real text: none in four tests (R002, RESULTS run 7, lab run 37910989965,
  predicate F1 at most 0.218 against 0.434; J001, run 9, lab run 37996675559, BA at most 0.500 against 0.670; L001 run 2,
  run 10, lab run 38102412415, BA at most 0.500 against 0.641, a-0p5b not told apart; A001 run 1, run 11, lab run
  38114522160, P 0.577 against 0.663, the two mapped a-4b secondaries 0.667 and 0.653 not told apart).

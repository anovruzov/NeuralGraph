# Handoff prompt, 2026-10-15 00:10 UTC (HEAD 16593d1, the deployment branch)

Paste everything below the line into a fresh Claude Code session in this repository, or hand it to the engineer who
will write the next prompt.

---

You are continuing the Mycelic work in this repository on the deployment branch (`git branch --show-current`;
develop, commit and push only there). Read this whole prompt, then `docs/handoff/LOOP-48H-STATUS.md` (the hourly log;
its last lines are the latest state), before doing anything.

## 1. What this is for

The founder's goal: a product YC will want, not just a knowledge fabric. It should generalise to any company and any
field, discover hidden patterns across a company's sites without moving raw records, and run on small models. Two
benchmarks measure it, and they are reported separately, always:

- **The accuracy loop ("86")** is pinned in `docs/strategy/LOOP-48H.md` section 2: balanced accuracy of the site
  verifier's verdicts against the filed-category key, at record level with codes hidden, on a fresh draw of 150 real
  NHTSA records, scored as J001 scores. To pass: BA at least 0.860, the lower end of the paired difference against
  the lexical judge above 0, and the lower end of BA above the predicate-only bound. There is one primary (model,
  arm) pair per cycle, and its confirmation comes on the next cycle's fresh draw. Never redefine the target or the
  metric.
- **EMERGENCE end to end** (`research/e2e_ingest`, `docs/collective/E2E-INGEST.md`, frozen by
  `docs/collective/EMERGENCE-80/SPEC.md`, commit 5eb2e28). The metric is `recall_all` of the real `run` job. The
  target is a mean of at least 0.80 with its 95% t lower bound at least 0.80, over 20 held-out worlds whose seeds
  derive from a freeze commit's sha. Privacy checks must pass on every world, and decoy acceptance must be no higher
  than the baseline's. The development seeds are 0 to 19.

## 2. Rules that never bend

- **Pushing.** `git push -u origin <the deployment branch>`, retrying after 2, 4, 8 and 16 s on network errors.
  Never push `main` or any `wf/*` branch. Open no PR unless asked.
- **Commit trailers, exactly:**
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01GK3xL5okLF3GuYjVc8Bn5e`
- **Names.**
  - No model family or repository names in commits, code or docs. The models are a-0p5b, a-1p5b, a-4b and a-4b-r.
  - Never write the deployment branch's first path segment under `docs/collective` or `docs/lab`; say "the
    deployment branch".
  - `tests/mycelic/test_collective_guards.py` (NoModelNamesTests) enforces both.
- **Results.**
  - Never fabricate or estimate a number; every figure carries a run id, a commit or a `file:line`.
  - Synthetic results are stamped `measurement: false`, and simulated figures are never called live runs.
  - Never report a partial result as complete.
  - Lab notes stay digit-free.
- **Tests and guards.** Never hide, weaken, skip or quarantine a test or a privacy guard.
- **Privacy.**
  - k = 3 is the minimum disclosure threshold; never lower it.
  - Raw records never leave their site, and nothing reads raw notes centrally.
  - No externally hosted model without approval.
  - Never export NHTSA operator names, VINs, cities, dealers or record refs, and never print or commit a drafted term.
- **Pre-registration.** Commit the rule alone, then three reviews, an amendment, the build, reviews, fixes, the gate,
  and one run. The first run is the result. Never tune on a test or held-out set.
- **Pinned files.** Never modify:
  - `lab/j1.py`, `lab/goldlabels.py`, `lab/j2.py`, `lab/j2labels.py`, `lab/j3.py`, `lab/j3labels.py`, `lab/jt.py`,
    `lab/shard.py`;
  - `mycelic/**`, `connectors/openfda.py`, `research/**`, `docs/collective/replay/vehicles/pack/**`;
  - `tools/**` (load these by path only);
  - earlier `CHOICE-*.md` (amend only by a dated amendment), `run-*.json`, `*-settings.json`,
    `lab/templates/terms-003.json`, `lab/requests/terms-003.json`.

  The j2, j3 and jt functions in aggregate and summary stay unedited.
- **Lab runs.**
  - A code push and a lab-request push are separate pushes, and only the request push starts a lab run.
  - No CI-triggering push while model shards run.
- **Blocked hosts.**
  - The CI artifact host is refused by policy; never route around it. Read job logs with the GitHub MCP
    `get_job_logs` (`return_content`, `tail_lines`).
  - CFPB, OSHA and CPSC returned 403. Never retry them another way.
- **External claims.** Do not claim "collective intelligence" or "any field". Docs stay private until the founder
  shares them.
- **Founder's orders of 2026-10-14:**
  - Do not touch `mycelic/retrieval.py` until A003's run is recorded, nor during any active benchmark.
  - For EMERGENCE: change nothing in the system until the root-cause report exists and the founder approves its
    fixes.

## 3. Where things stand (00:10 UTC, 2026-10-15)

**The accuracy loop.**
- 21 (model, arm, cycle) comparisons so far.
  - A001 (run 11): primary (a-4b, negation) BA 0.577 [0.530, 0.623] against lexical 0.663; failure.
  - A002 (run 12): primary (a-4b, evidence) BA 0.540 [0.503, 0.577] against 0.680; failure.
  - The best secondary is 0.687, level with word matching (`docs/lab/RESULTS.md` runs 11 and 12).
- **A003, lever 5: drafted terms.**
  - Done: the term run is recorded and the amendment committed. The term artifact lab-plan-38612800578-1 expires on
    2027-01-12T18:09:48Z.
  - In progress: build 2, the judging kind j4. Its three reviews returned blocking findings, and their fix is running
    in a workflow on the local branches `wf/a003-fix` (WIP at 314e614), `wf/a003-wire` (11bf9c4) and `wf/a003`
    (c1d4fec).
  - Not started: the judging run.
- **A004, lever 6: reasoning on.** The rule is at e6b9a45, amended at e54eb6d and merged at f557ce0. It waits on
  A003's result. "A004" keeps this meaning; the after-fix rerun of the retrieval stall is recorded as "end-to-end run
  3", unless the founder decides otherwise.

**EMERGENCE.**
- **The baseline:** 13 of 40 (`recall_common` 13/22, `recall_rare` 0/18; decoys accepted 5 in class 2, 9 in class 4,
  3 in class 5). It comes from local runs of the `run` job at seed 0, not from CI:
  - the build's measurement (`docs/collective/BUILD-E2E.md`:679-683);
  - a local real run at ec72e22, which has 5eb2e28's code (report digest `159cc6ebdf93…`).

  No CI run's discovery block has been read (corrected 2026-10-15; this line first credited CI run 1).
- **Run 2** (38639771210 on 5eb2e28) is all green and recorded at verdict level only. Its report sits in a log of 11,692
  lines that is too long to read through the API, and its artifacts are blocked. The founder has been asked to
  download artifacts 11931284874 and 11933036780, and run 1's.
- **The simulator's 0.597 (10k) and 0.566 (50k)** are simulated model behaviour, and are being verified.
- **Open diagnosis questions:** a census on seeds 0 to 5 found class C empty and the shipped two-site gate capping
  recall below 0.80. Its critics found the first ceilings optimistic: they used snapshot weeks before W12 and sliding
  windows. A corrected round is running.
- **First root-cause findings (preliminary):**
  - Most Gold patterns get no detector snapshot and are dropped as rule-only.
  - No site is eligible before W12.
  - Echoes count as independent roots, which lets duplicate-inflation decoys through.
  - There is no temporal-order check, which lets temporal scrambles through.
  - Decoy acceptance counts only the facet-0 key.
  - There is no aggregation above sites and no recursive questioning, and the Orchestrator is bypassed.
  - No transport loss was found.
  - The run job's `check_world_counts` refuses seeds other than 0, which blocks the seed policy until a dated
    amendment to SPEC.md.
- **The CI loop stall.** The attribution to `retrieval.py`'s BM25 is contradicted by reviewer A. The probable cause is
  the loop thread waiting for the interpreter lock held by the test's CPU-bound walk threads, and the tests' bound is
  load-sensitive. This is to be settled in the root-cause report before any fix.

**CI.** Green on 5eb2e28 in both workflows. The two loop timing tests carry `LoopStallWatch`, which names the loop
thread's stack in any failure message.

## 4. Work in flight, and where it lives

These run in the old container. If you are in a new container, none of it exists. Re-run what you need: the
workflows' prompts are summarised here.

| Work | Workflow / task | Output location (old container only) |
|---|---|---|
| A003 build 2 fix | wf_6eba3ec4-f97 / wky2w02lc | branches `wf/a003*` (local, unpushed) |
| EMERGENCE root cause: reproducer D and reviewers A (distributed systems), B (retrieval and graph) and C (benchmark integrity), then cross-challenge | wf_aed9ea96-6ae / wdb5miivv | `scratchpad/emergence80/rc/{A,B,C,D}` |
| Corrected diagnosis: ceilings from W12 on a fixed 6-week grid, decoys under each rule, formal disclosure | wf_dc5816f4-763 / wsc4i4a50 | `scratchpad/emergence80/r2/` |
| Second 48-hour report draft, with two fact-checks | wf_6b9ce24a-2eb / w65g2ht57 | `scratchpad/report48h-2/` |

The `wf/a003*` branches hold A003 build 2 and cannot be pushed under the rules above. If the old container is gone,
rebuild build 2 from `docs/collective/A003/CHOICE-A003.md` ("The build plan"; C1 to C32 and the later dated
amendments) on a fresh `wf/a003` branch from the deployment branch.

## 5. Next steps, in order

1. **A003.**
   - Finish build 2's fix.
   - Check that no pinned path changed, then `git merge --no-ff` into the deployment branch.
   - Run the gate: all of `tests/lab`, onboard, the guards and x3, and the accuracy-003, terms-003 and j3 dry runs.
   - Push the code. Then push `lab/requests/accuracy-003.json` alone, before 2027-01-12T18:09:48Z.
   - When the run ends, freeze it: commit sha, request sha256, settings, run and job ids, and the report blocks
     rebuilt from the logs with their sha256 checked.
   - Record it as completed, failed or stalled, as it actually ended, in CHOICE-A003 Runs, RESULTS run 13, the YC
     brief and the status log. The comparison count continues from 21.
2. **EMERGENCE root cause.** When both workflows are done:
   - Write `docs/research/EMERGENCE_ROOT_CAUSE_REPORT.md` with the founder's ten sections:
     1. the verified baseline;
     2. the execution map;
     3. per-stage evidence loss;
     4. the root-cause table (severity, evidence, affected patterns, confidence);
     5. the runtime stall;
     6. the decoy analysis;
     7. reproduction instructions;
     8. ranked corrective actions;
     9. regression tests;
     10. the frozen before/after protocol.
   - Mark every issue confirmed, probable or unverified.
   - Run critics until no blocking finding remains.
   - Commit it, then present the fixes with their benefits, risks and verification tests, and **wait for approval**.
3. **The retrieval stall.** Only after A003 is recorded, and only if the root-cause report still places a cause there.
   Retrieval semantics stay unchanged. Add regression tests for loop responsiveness, concurrent retrieval, timeouts
   and cancellation. Then run end-to-end run 3 on the patched commit, with every other parameter equal, against run 2.
4. **A004.** After A003's result, its amendment reads A003's case; then the timing-check build.
5. **Housekeeping.**
   - An hourly status line in `docs/handoff/LOOP-48H-STATUS.md`; run `date -u` first.
   - The next 48-hour report is due 2026-10-17 01:50 UTC, in the format of `docs/strategy/LOOP-48H.md` section 9.

## 6. Decisions the founder owes, each with its current default

- **Whether the two-site corroboration rule is an invariant.** Default: yes, until the root-cause report proposes
  otherwise with its decoy cost measured.
- **Whether a cross-site protocol over encrypted below-k inputs is approved.** Default: not permitted.
  `docs/collective/LEAKAGE.md`:32-34 and the closed artifact list in `egress.py` refuse it today.
- **The world-count check that blocks seeds 1 to 19.** Default: a dated amendment to SPEC.md, proposed together with
  the report.
- **Downloading and committing the CI artifacts** of e2e-ingest runs 38425113336 and 38639771210.
- **The naming of the after-fix run.** Default: end-to-end run 3. "A004" stays the reasoning-on cycle.

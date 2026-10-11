# The 48-hour loop: prompt, what is next, and the distance to a production pilot

Written 2026-10-11, 01:50 UTC, from four research reads of the repository (ingestion path, accuracy evidence and
baselines, pilot readiness, cycle times), a draft, three adversarial reviews (gameability, feasibility, executability)
and a revision. Private until the founder shares it. The founder's words: "Create a prompt that will essentially allow
a 48-hour loop and goal of achieving 80% accuracy against centralized baseline, as well as also ensuring end-to-end
neural graph ingestion through Tesseract into NATS into the mycelial fabric"; later the same day: "Loop until we
hit 86." The target below is 86.

## What 86 is measured against (the pinned reading)

The prompt pins "86 against a centralized baseline" as balanced accuracy 0.860 of the site verifier's confirm/refute verdicts against the filed-category key (the codes the filer attached, which HQ already holds centrally and which score 1.000 by construction), at record level on a fresh pre-registered draw of 150 real NHTSA complaints with codes hidden, scored exactly as J001 was, with one primary (model, arm) pair per cycle, paired 95% intervals above the lexical judge (0.670 in J001) and above the predicate-only bound (0.580), and a confirmation on the next cycle's fresh draw before any success ends the loop. This reading was chosen because it is the only candidate where readers that read nothing sit far below 0.86 (record-blind 0.527, bound 0.580), it needs no key, no pinned-code edit and no hosted model, one run takes about 22 minutes on real public data, and it measures the one thing that has failed in every real-data test so far (runs 7 and 9, and now L001 run 2, where every small model scored at or below word matching). It explicitly rejects site-level BA as primary because the route-role baseline reaches 0.808 there without reading a record, and it forbids any arm that lets the judge see the codes. The founder may instead have meant 86% of a larger hosted model's chance-corrected score (needs the two secrets, a hosted manifest entry, lifting `NHTSA_HOSTED`/`MSHA_HOSTED`, and a new hosted-central lab role), 86% of the pooled national channel's detection recall (model-free, says nothing about reading), or gate-status agreement with the key (5 items, a count); the prompt asks for confirmation at hour 0 and runs the pinned reading by default without switching after a result.

## What is next

State verified at 2026-10-11 01:45 UTC on `claude/mycelic-production-deployment-r93cez`, HEAD `abbf2e4`, clean tree, 0 ahead / 0 behind `origin` (`git rev-list --left-right --count`). The draft and the review findings are both stale: K14 is merged (`3fd230f`) and pushed; the guard probe (run 38102272696, 01:34-01:35 UTC) passed; `lab/requests/latency-002.json` is pushed (`abbf2e4`, 01:36:39 UTC); and **L001 run 2, lab run 38102412415, completed with success at 01:44:20 UTC** (plan job 01:36:45-01:38:33, all 15 shards sealed with exit 0 in 25.7-199.4 s, aggregate job 114362048048 01:43:50-01:44:20). It is not recorded: `docs/lab/RESULTS.md` ends at run 9 (`:314`) and `docs/collective/L001/CHOICE-L001.md` `## Runs` (`:948`) holds run 1 only. The local branches `wf/l001-k14`, `wf/l001-k14-build`, `wf/l001-k14-fix` are ancestors of the pushed HEAD, so nothing depends on them; `749cae6` is an orphan WIP superseded by `c6b5ab0`. `docs/collective/E2E-INGEST.md`, `research/e2e_ingest/` and `docs/handoff/LOOP-48H-STATUS.md` do not exist.

1. **Loop agent, hour 0-1: record L001 run 2.** Read the aggregate job's `report.md` block (the GitHub MCP `get_job_logs` tool with `return_content` on job 114362048048 returns it between `=== MYCELIC-LAB report/report.md BEGIN lines=146 sha256=e8b3960786ca… ===` and its END line; the sandbox `gh api` refuses the log redirect). What it printed, to be re-read and verified before it is written down: comparators from the plan job, identical to run 1's as K14 expected (lexical 0.641 [0.403, 0.840], route-role 0.808 [0.667, 0.938], record-blind 0.551 [0.379, 0.714], key 1.000, predicate-only bound 0.603, empty draws 0.000); a-0p5b BA 0.500 [0.500, 0.500], sensitivity 1.000, specificity 0.000, unknown 0.000, minus lexical -0.141 [-0.340, 0.097], minus route-role -0.308 [-0.438, -0.167], headline `not_told_apart`, gate decisions equal to the key's 0 of 5; a-1p5b 0.417 [0.333, 0.500], unknown 0.161, minus lexical -0.224 [-0.436, -0.007], minus route-role -0.391 [-0.533, -0.256], `worse`, 1 of 5; a-4b 0.417 [0.333, 0.500], unknown 0.323, the same two differences, `worse`, 2 of 5; every model 5 of 5 questions, 0 of 31 site answers left out; alert-question time to answer 22.7 s / 35.8 s / 143.1 s with judge-call medians 0.62 / 0.94 / 3.79 s over 36 model calls, no contended or unfinished question. Write RESULTS.md `## Run 10`, CHOICE-L001 `### Run 2`, a handoff status line (never the decisions column), create `docs/handoff/LOOP-48H-STATUS.md` with the open asks below, commit, push. This is the second real-data judge test in which every small model scores at or below the lexical judge, and the first at site level: all three sit below the route-role baseline that reads no record.
2. **Founder, hour 0, one line each:** (a) "NEEDS FOUNDER: 86 is read as balanced accuracy 0.860 of the site verifier against the filed-category key, record level, codes hidden, fresh real NHTSA draw (PROMPT §2); confirm or name one of §2.7." The loop runs §2 by default and will not switch after a result. (c) May the loop open a PR or push to `main`? Default no. (d) Hosted model: the two secrets, a `kind: hosted` entry with `context_tokens`, and lifting `NHTSA_HOSTED` / `MSHA_HOSTED` (`lab/request.py:234, 237`) are necessary but not sufficient: `J1_KINDS_PROBLEM` (`:236`) and `HOSTED_PLACE` (`:232-233`; `lab/hosted.py:5-7`) refuse a hosted judge, so the loop would also build and review a hosted-central role for j2 (about one review cycle) before any hosted comparator runs on real text. (e) The STRATEGY 9.1 ruling on the fictional demo and the docs-honesty text for `site/index.html:289` (`HANDOFF-2026-10-09.md:346-348`).
3. **Chief scientist, before the J2 rule is committed (target hour 2):** decision 9 (how "unclear" counts; `HANDOFF:372`). The repo's table has no call column (`HANDOFF:361-372`: `#`, `Decision`, `Options`, `My recommendation`); a call reaches the loop only as a message. Default if silent: the shipped `decide` (`mycelic/collective/edge/verify.py:243-259`) is primary and reply mappings are declared secondaries. Also within the week: the replacement for the corrected chance null (plan 1.1, `HANDOFF:259-268`), detector settings per site count (1.4-1.5, `HANDOFF:266-271`), R003 settings (`HANDOFF:329-331`), and decisions 1, 2, 7 for Tesseract.
4. **Loop agent, accuracy track, hours 0.5-8 then repeating:** J2, a J-type judge test on a fresh draw of real NHTSA complaints with codes hidden, as a new lab kind `j2` (new `lab/j2.py`, `lab/j2labels.py`, plus unpinned wiring; never `lab/j1.py` or `lab/goldlabels.py`, J001's `CODE_FILES`, `lab/j1.py:136-141`), with the draw, the exclusion of J001's 150 records, one primary (model, arm) pair and the success clause pinned verbatim from PROMPT §2. Rule alone on `wf/j2-design` → adversarial review → amendments → build → build review → fix → merge (`pytest tests/lab`, about 21 min, and the L001 template dry run, about 6 min, before merge) → request push alone. J001 took 4 h 25 min rule-to-record with a 21.5 min run (run 37996675559); L001 run 2 took 7 min 38 s. Expect each cycle to miss 0.860 (best record-level model so far 0.500 vs lexical 0.670, `docs/lab/RESULTS.md:326-331`) and report the measured number and gap. Nothing in the loop may re-score J001 or draw against the printed comparators.
5. **Loop agent, ingestion track, in parallel via Workflow:** S0 restore CI on this branch (`mycelic.yml` branch trigger with `research/**`, `lab/**`, `.github/workflows/**` added to the paths filter, kept before `pull_request`; the two tests that read the file, `tests/mycelic/test_packaging.py:130-131` and `tests/onboard/test_onboard_demo.py:1361-1365`, run locally first), S1 `docs/collective/E2E-INGEST.md` committed alone and reviewed, then the shipped-path bridge first (S3 `publish_api.py`, S4 `hq_bridge.py` over `pd_verdicts` / `pd_conclusions`, `mycelic/collective/detect/store.py:127-136`, S6 drills, S8 workflow), then the Tesseract leg (S2, S5, S7). Cut rule: if by hour 24 fewer than one J-cycle is recorded, the Tesseract leg moves to the next 48 h and Goal B is reported as "bridge criteria met or not; Tesseract leg not reached".
6. **Founder, this week: start discovery.** `docs/strategy/DISCOVERY.md:5` and `OUTREACH.md:5`: no call made, nothing sent. A design partner's export is the dominant pilot blocker (PILOT_DISTANCE). Nothing in the loop substitutes for it.
7. **Loop agent, hours 40-48:** record every run (RESULTS run sections, CHOICE `## Runs` with the running comparison count, BUILD "as built", YC brief with measured numbers only, handoff status lines), suites on the final tree, push, final report in `docs/handoff/LOOP-48H-STATUS.md` in PROMPT §9's format, then continue with the next pre-registered lever unless the founder says stop.

## Distance to a production pilot

**Verdict.** The pilot artefact is closer than the research record suggests; the humans are not. The signal audit (`mycelic/collective/pilot/audit.py`) and `pilot.start` are built, stdlib-only (`tests/mycelic/test_collective_guards.py:75, 138-149`), guarded, tested (133 passed at HEAD in 97.7 s across `tests/onboard/test_pilot_start.py`, `test_collective_pilot.py`, `test_collective_power.py`), and run end to end in about 3 s on a synthetic export, matching `docs/collective/PILOT.md:134-165`. The pilot path uses **no model**: extraction is lexical (`audit.py:17-19`). The offer, LOI and discovery kit are written. But no discovery call has been made and nothing has been sent (`docs/strategy/DISCOVERY.md:5`; `OUTREACH.md:5`), no partner export exists (`YC-BRIEF.md:4, 126`), `pilot.start` has run only on synthetic exports (`YC-BRIEF.md:108`), the power gate fails on the only background that exists (`POWER.md:63-65, 76-87`), and the LOI's success test rests on a chance null the chief scientist has not replaced (`OUTREACH.md:68-69`; `HANDOFF-2026-10-09.md:259-268`).

**Calendar estimate.** First **unpaid** production pilot (the audit run inside a partner's walls on their own history): about **10-14 calendar weeks** from the day outreach starts, assuming 10-20 discovery calls over 4-6 weeks, one "yes" to a free 4-week audit, 2-4 weeks of partner security/legal review and export preparation partly in parallel, and the agent loop finishing packaging, the data-handling note, export compatibility and a generic power check meanwhile. A **paid** phase follows only if the audit beats its pre-agreed test; it is not estimable today and may never come if the power check at the partner's volumes fails as it does on public-data volumes.

**Dominant item: A1, a design partner's export.** Second: A4, the chance-null decision, which determines whether the pilot's verdict means anything once it runs.

**Blockers, grouped.**

Founder / chief scientist (human):
- A1 Design partner: discovery calls, then one export (2 years, ≥3 sites) and past CAPAs. Zero calls, zero emails (`DISCOVERY.md:5`; `OUTREACH.md:5`). 4-8 weeks of founder time to a signed LOI.
- A2 Signed LOI with the pre-agreed success test; template ready (`OUTREACH.md:56-72`). 1-2 weeks inside A1.
- A3 Partner security/legal review (DPA/NDA, retention/deletion); nothing drafted on our side (`SECURITY.md` covers the fabric only, `SECURITY.md:1-8`). 2-6 weeks, partly parallel.
- A4 Chief scientist: replacement for the corrected chance null (plan 1.1; its synthetic criterion failed 5 of 20 vs 18, `PILOT.md:268-272`). Days to decide; gates the meaning of the pilot.
- A5 Chief scientist: detector settings per site count (1.4, 1.5; `HANDOFF:266-271`) and decisions 3, 4, 9 (`HANDOFF:361-372`).
- A6 Founder: STRATEGY 9.1 ruling on the fictional demo (`demo/collective/README.md:9-12`); withdraw or keep "public data is spent" (`MARKET.md:302`); publish the docs-honesty fix (`site/index.html:289`; `HANDOFF:346-348`). Hours.
- A7 Founder: hosted-model key (optional; the standing rule forbids alternative egress; necessary but not sufficient, PROMPT §7).

Engineering (agent loop; pinned code untouched):
- B1 Partner-installable pilot: no `pyproject.toml`/`setup.py`; install is clone plus `pip install -r requirements.txt` (`CONTRIBUTING.md:8-13`); CI is ubuntu-only (`mycelic.yml:12-68`). 2-3 agent-days for a stdlib-only zipapp or wheel with sha256, an INSTALL/RUN page, Windows/macOS CI.
- B2 Partner-facing data-handling and security note for the pilot (what is read/written/deleted, no network, what `--work` keeps, X5 disclosure, pack k). Missing. 1-2 days; private until the founder shares.
- B3 Export compatibility: .xlsx reader, D/M/YYYY by declaration, a `--preflight` (`PILOT.md:79-82`). 2-3 days.
- B4 Generic power-check background from a partner export; today only vehicles (`POWER.md:17`). 3-5 days plus a CHOICE file.
- B5 Pre-registered P001: `pilot.start` on a real public export on a runner, to retire "synthetic only". 2-4 days.
- B6 L001 run 2: **done** (lab run 38102412415, 2026-10-11 01:36-01:44 UTC, success; unrecorded at HEAD `abbf2e4`). Informative, not a pilot blocker: the pilot uses no model.
- B7 Docs honesty 5.1 text. 0.5 day; publishing is the founder's.
- B8 Implementation of A4/A5 once decided, as new code beside the pinned modules. 3-5 days after the decision.

Evidence / tests that must pass first:
- C1 Power gate at the partner's volume, sites and look-back; fails on the only background: at 0.1 ramps a week over 2 years X, S and R_mf find 0 of 30 (P 4, PRR 6; over 4 years P 6, PRR 7); no cell passes the 80% gate at a 26-week look-back; the best model-free cell is R_mf 20 of 30 at 2.0 a week; pooled P catches 36 of 60 sextuplings (`POWER.md:63-65, 78-84, 113`; `PILOT.md:282-297` makes a passing check a precondition). Needs A1's data shape plus B4; one run, hours.
- C2 A pre-registered real-data run of `pilot.start` (B5). Not run.
- C3 The chance-null test on reactive worlds; failed as written. After A4 and B8.
- C4 L001 run 2: **complete**. Result as the aggregate job printed it (job 114362048048, `report.md` sha256 `e8b3960786ca…`, to be verified when recorded): a-0p5b BA 0.500 [0.500, 0.500] "not told apart" from the lexical judge (0.641); a-1p5b and a-4b 0.417 [0.333, 0.500], "worse"; all three below the route-role baseline 0.808 [0.667, 0.938] that reads no record; gate decisions equal to the key's 0, 1 and 2 of 5. Latency on the alert question: time to answer 22.7 s, 35.8 s, 143.1 s on shared runner CPUs.
- C5 Any evidence that small models beat word matching on real text; negative so far and now three for three (predicate F1 0.03/0.18/0.22 vs 0.43, run 7; record-level BA 0.50/0.48/0.39 vs 0.67, run 9; site-level BA 0.50/0.42/0.42 vs 0.64, L001 run 2). Not required for the audit pilot; required before promising model verification.
- C6 X5 mitigation if partners reject presence disclosure; membership inference advantage 0.971 on synthetic worlds (`LEAKAGE.md:634-657`). New prereg; weeks; product, not pilot.

Not found: any partner-facing DPA/NDA/retention text; a run of `pilot.start` on real data; a generic power background builder; a `mycelic.yml` run on this branch since 2026-09-29; a record of L001 run 2 in `RESULTS.md` or `CHOICE-L001.md` at HEAD.

## The prompt (paste into a fresh session)

# Mycelic 48-hour loop: 86 accuracy against the centralized key, and end-to-end ingestion into the fabric

You are an autonomous agent running a 48-hour loop in the git checkout `/home/user/NeuralGraph`, branch `claude/mycelic-production-deployment-r93cez`. You have: this repo; the lab (GitHub Actions workflows in `.github/workflows/`, `mycelic-lab.yml` starts on a push that touches `lab/requests/*.json` or on `workflow_dispatch`, `mycelic-lab.yml:3-7`); the Workflow tool for substantive subtasks; `send_later` for self check-ins; the GitHub MCP tools and `gh api`. You have no prior context. Everything you need is below or in the files named. Use `date -u` for the time. The loop's clock starts at your first command.

## 0. Goals and the stop rule

- **Goal A (accuracy).** Reach **86** on the pinned metric in §2, with the interval and confirmation rules met, in pre-registered runs on real public records.
- **Goal B (ingestion).** All seven done-criteria in §3.1 pass in recorded CI runs on this branch.

Stop only when (i) both goals are met as defined, or (ii) the founder says stop. If 48 hours pass first: write the final report (§9), push it, and keep looping on the next pre-registered lever in §2.6. The founder said "Loop until we hit 86." Never redefine the target, the metric, the data class, the draw, the comparators, the interval rule or the confirmation rule while looping. If a definition turns out to be unmeasurable, say so in the report and ask the founder; do not quietly swap it. The founder has not yet confirmed that §2 is what "86" meant: ask at hour 0 (§7) and run §2 by default.

Honesty: the evidence says 86 is far away. On the record-level metric the best small model scored 0.500 and the lexical judge 0.670 (`docs/lab/RESULTS.md:326-331`, run 37996675559). At site level (L001 run 2, lab run 38102412415, 2026-10-11) a-0p5b scored 0.500 [0.500, 0.500] and a-1p5b and a-4b 0.417 [0.333, 0.500] against a lexical judge at 0.641 and a route-role baseline at 0.808 that reads no record. Two ceilings go with every Goal A number: the filed codes are noisy and the key's own noise ceiling is unmeasured (`docs/collective/replay/vehicles/CHOICE-J001.md`, trap 1), and the models may have seen these public complaints in training, which helps a model but not the lexical judge (trap 7). Your job is to run pre-registered levers and report measured numbers, not to find a definition that passes.

## 1. Standing rules (never break any of them)

1. **Pre-registration.** For every real-data test: the CHOICE file (rule) is committed alone before any code; an adversarial review; amendments committed before any run; build; build review; fix; one run. **The first run is the result.** Never re-run the same rule for a better number. Never tune on the test set: every cycle uses a fresh draw, and records judged in an earlier cycle are excluded from later draws by the mechanism in §2.3. Never fabricate numbers, never round in a favourable direction, never hide a failing test or a failed run. Report failure plainly, with the number, the interval and the gap.
2. **Egress.** No alternative model-host egress. Never circumvent proxy blocks (refused so far: CFPB, OSHA, cpsc.gov/Data 403s, a blob host, MSHA from the sandbox). Real MSHA/NHTSA data is fetched only on GitHub runners.
3. **Claims.** Do not claim "collective intelligence" or "any field" externally. Nothing in `docs/strategy/YC-BRIEF.md` or `site/` gets a number that was not measured in a recorded run.
4. **Hash-pinned code must not change:** `mycelic/collective/*.py` (top level); under `mycelic/collective/`: `detect/`, `edge/`, `packs/*.py`, `evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/`, `onboard/`; `connectors/openfda.py`; `docs/collective/replay/vehicles/pack/`; `pilot/audit.py`, `pilot/power.py`; earlier experiments' `CHOICE-*`, `run-*.json`, `*-settings.json`. New behaviour goes in new modules. The routing spike's code hash (`research/routing_spike/run.py:73-75`, `CODE_PATTERNS`) also covers `research/routing_spike/*.py`, `NeuralGraph/research/coordination/*.py`, `NeuralGraph/research/retrieval/*.py`, `NeuralGraph/chat_memory/llm.py`, `NeuralGraph/chat_memory/textutil.py`, `mycelic/collective/**/*.py` and `mycelic/*.py`: do not edit those either; import them. J001's code hash (`lab/j1.py:136-141`, `CODE_FILES`) covers `lab/j1.py` and `lab/goldlabels.py`: never edit them. The hash-pin tests that prove the pins held: `tests/mycelic/test_collective_guards.py`, `test_collective_demo.py`, `test_collective_evaluate.py`, `test_collective_onboard.py`, `test_collective_pushdown.py`, `test_collective_x3.py`, `test_integrity.py`, `tests/onboard/test_onboard_d003.py`; refresh the list with `grep -rlE '[0-9a-f]{64}' tests/mycelic tests/onboard tests/lab --include='*.py'`.
5. **Privacy guards are never weakened to pass a test:** k-suppression, the Boundary, the report guards, the import guards (`tests/mycelic/test_collective_guards.py`; 74 collective modules must import with the standard library only, `:635`). Never print MSHA operator/controller names or ids or narratives, nor NHTSA operator names, VINs, cities or dealers, anywhere: logs, commits, docs, replies, reports. Use the lab's labels (c1, s01, mine labels, a-0p5b, a-1p5b, a-4b).
6. **Branch and pushes.** Develop only on `claude/mycelic-production-deployment-r93cez`, with workflow branches `wf/<x>-design`, `wf/<x>`, `wf/<x>-fix` merged back into it. Push with `git push -u origin <branch>`. No PRs unless asked. Never push to `main` and never merge anything to `main` without the founder's explicit say-so; ask once, in one line, and continue while waiting.
7. **Commit trailers** on every new commit: the two lines the session's attribution reminder gives, currently
   `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
   `Claude-Session: https://claude.ai/code/session_01GK3xL5okLF3GuYjVc8Bn5e`
   (the model name in the first line follows the session's model; earlier commits carry the name of the model that wrote them, which is correct, never rewrite them). No model identifiers in commit bodies or code: use a-0p5b, a-1p5b, a-4b.
8. **The handoff's decisions table** (`docs/handoff/HANDOFF-2026-10-09.md:361-372`) belongs to the chief scientist. Its columns are `#`, `Decision`, `Options`, `My recommendation`; calls are recorded "in the shared doc's last column" (`:361`), which is outside the repo. Never add a column, never write a value, never read a recommendation as a call. A call reaches you only as a message from the founder or the chief scientist; record it under `## Decisions received` in `docs/handoff/LOOP-48H-STATUS.md` with the date. Decisions 1, 2, 7 and 9 bear on this loop; absent a call, §7's defaults apply.
9. **Docs are private** until the founder shares them. Write them; do not publish them.
10. **Commit and push WIP at the end of every stage.** Two container restarts in 36 hours each cost about an hour of unpushed work (`docs/collective/onboard/BUILD-D003.md:376-378`).

## 2. Goal A: the pinned definition of "86 accuracy against a centralized baseline"

### 2.1 What 86 is a percentage of
86 = **balanced accuracy 0.860** of the site verifier's verdicts against the **filed-category key**, on a scale where the key itself scores 1.000. Balanced accuracy = (sensitivity + specificity) / 2, scored exactly as `docs/collective/replay/vehicles/CHOICE-J001.md` scores J001: for each record one question about a filed category (positive) and one about a category the record was not filed under (negative, drawn with the frequency of the other records' positives, `lab/j1.py:130` `NEGATIVE_DRAW`); a `confirm` on a positive is correct, a `refute` on a negative is correct, `unknown` is never correct. Intervals: cluster bootstrap over records, B 10,000, 95%.

### 2.2 The centralized baseline, concretely
The **filed-category key** is the set of category codes the filer attached to each record. These codes are what a centralized system already holds (HQ's own `codes`-channel cells; `docs/collective/L001/CHOICE-L001.md`, K2). A centralized reader that sees the codes scores 1.000 by construction. The federated reader sees only the record text, codes hidden, inside the site boundary. "86 against the centralized baseline" therefore means: the site verifier recovers the centralized answer at balanced accuracy 0.860 while reading nothing but its own record.

### 2.3 Data, draw and path (pinned; any change defines a different data class whose result can never count toward 86)
- **Population:** real public NHTSA complaints of the vehicle pack `docs/collective/replay/vehicles/pack`, the makes `NHTSA_MAKES` and the window `NHTSA_WINDOW` of `lab/goldlabels.py:49-50` verbatim, the j1 eligibility rule (a narrative, one vehicle, at least one specific component code; `goldlabels.py:80-83`), codes hidden (`lab/j1.py:275-279` refuses a record that still holds codes).
- **Size:** n = 150 records (300 questions) per cycle; `EXCLUDED` (`j1.py:118`), `TWINS` (`:120-121`), `NEGATIVE_DRAW` (`:130`) and the record-blind `PRIOR_RULE` (`:133`) unchanged. The labels seed, the question seed and the bootstrap seed are written in the cycle's CHOICE file before the request is pushed.
- **Fresh draw, verifiable:** the lab draws `random.Random("nhtsa:<seed>").sample(eligible, n)` from a complaint file downloaded live in the plan job (`goldlabels.py:90, 133`), so a new seed alone gives no disjointness, and record refs may never be committed (the report guards refuse record ids). Exclusion therefore happens on the runner, in the j2 plan job, in a new module `lab/j2labels.py`: download J001's plan artifact from run 37996675559 (artifact retention 30 days, `lab/requests/judge-001.json` `retention_days` 30, so until about 2026-11-08; `tools/l1/guard_probe.py` already downloads another run's plan artifact this way) and, as a second check, rebuild the seed-1 n-150 draw and stop unless its labels sha starts with `bc6d092d8bca` (`RESULTS.md:320`); remove those record refs from `eligible` before sampling; print the `available` count (`goldlabels.py:143`), the excluded count and the overlap count (expected 0), never a ref. Each later cycle's CHOICE lists the run ids and labels shas of every earlier cycle, excluded the same way from their own plan artifacts. If neither mechanism can verify freshness, stop and ask; do not run. Record the first cycle's `available` count in its CHOICE Runs entry and stop pre-registering fresh draws when fewer than 150 eligible unexcluded records remain.
- **Path:** a-0p5b, a-1p5b, a-4b on the lab's runners through the product path: `judge_task`, `judge_payload` and `decide` in `mycelic/collective/edge/verify.py` (`:135-144`, `:162-168`, `:243-259`; pinned, unchanged). Lab-side variants (§2.6) go through `Runtime.run(task, payload, schema, ref=...)` (`mycelic/collective/inference/runtime.py:166-167`) with their own TaskSpec and are scored by the same pinned `decide`.
- **Codes never reach the judge in any arm:** no arm may place the judged record's codes, its filed categories, or anything derived from them in the payload, the task text or a reply mapping (the shipped `judge_payload` carries codes, `verify.py:165`; j1 strips them, `j1.py:275-279`). A codes-present arm (`HANDOFF:390`, Q10) may run only as a labelled product-realism secondary that never counts toward 86 or toward a provisional success.
- **Lab kind:** a new `j2` kind: new modules `lab/j2.py` and `lab/j2labels.py` plus wiring in `lab/request.py`, `plan.py`, `prereg.py`, `units.py`, `aggregate.py`, `summary.py`, `warmup.py`, `notes.py`, `.github/workflows/mycelic-lab.yml`, `docs/lab/REFERENCE.md` and `lab/requests/README.md` (`tests/lab/test_lab_docs.py` pins both docs). None of these is pinned; `lab/j1.py` and `lab/goldlabels.py` are. Before merge: `python -m pytest tests/lab -q -p no:cacheprovider` (about 21 min; run it in the background and poll) and the L001 template dry run (`python -m lab.dryrun --request lab/templates/latency.json ...`, about 6 min, `docs/collective/L001/BUILD-L001.md:485-491`). An in-flight lab run uses the workflow at its own commit, so merging j2 code does not disturb a running experiment.

### 2.4 Comparators (same records, paired, 95% cluster-bootstrap interval of the difference)
- **Lexical judge** (`lexical_judge`, `verify.py:171`): J001 0.670 [0.627, 0.713].
- **Predicate-only bound** (`lab/j1.py:30-33`; `CHOICE-J001.md:90-95`): a point value fitted to the draw's answers, no interval; J001 0.580.
- **Record-blind control** (`PRIOR_RULE`): J001 0.527 [0.467, 0.587].
Readers that read nothing score 0.527-0.580 here, far below 0.86, so this definition cannot be met structurally. The site-level version is rejected as primary: the route-role baseline, which reads no record and follows HQ's routes, scored 0.808 [0.667, 0.938] in L001 runs 1 and 2 (`CHOICE-L001.md` Runs; run 38102412415).

### 2.5 Verdict rule (copied verbatim into every J-cycle CHOICE file)
- **One primary pair.** Each CHOICE file names exactly one primary pair P = (model, arm), chosen from earlier recorded results before the run. Every other (model, arm) pair in the run is a secondary, reported with the same statistics; a secondary that meets the pass test is a hypothesis for the next cycle's primary, never a result.
- **Complete.** A verdict is read only when every planned record of the draw is scored, `measurement` is true, and the share of records dropped by transport failures is at most `WITHHOLD_SHARE` 0.01 (`lab/j1.py:128, 503-515`). Otherwise the cycle is **incomplete**: record it, and the next cycle needs a new rule on a fresh draw.
- **Pass test on P:** BA point estimate ≥ 0.860 **and** the paired difference to the lexical judge has its 95% lower end above 0 **and** the model's BA 95% lower end is above the predicate-only bound's point value computed on the same draw **and** the unknown share is reported.
- **Success (86 reached):** P passes in this cycle **and** P, named as the primary in the next cycle's CHOICE file before that run, passes again on that cycle's fresh draw. A first pass is **provisional**, shipped path included; only a confirmed success ends the loop.
- **Partial:** BA ≥ 0.860 but an interval clause fails; or BA < 0.860 but P beats the lexical judge with the lower end above 0. Report both numbers and continue.
- **Failure:** BA below the lexical judge, or the interval to lexical includes 0. Report plainly: "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p." Continue with the next lever.
- **Multiplicity.** Every CHOICE Runs entry and the §9 report print the running count of (model, arm, cycle) comparisons made since the loop began. With 150 records the BA standard error is about 0.035, so a sampling peak can cross 0.860 on some draw; the confirmation rule exists for that reason.
- **Secondary (report when available, never promote):** the chance-corrected share (BA_site − bound) / (BA_central − bound) against a hosted central reader, only when §7 allows one on real text.

### 2.6 Lever order (one CHOICE file per cycle; arms declared before the run)
From `docs/lab/RESULTS.md`, `HANDOFF-2026-10-09.md` and `verify.py`. A cycle may carry several arms if all are declared in the rule, with one primary pair (§2.5).
1. **Unclear handling** (decision 9). a-4b answered unclear on 51 of 150 positives and 94 of 150 negatives; counting unclear as refute after the fact gave 0.700 (`RESULTS.md:333-340`); it decides nothing and cannot reach 0.86 because 37 "yes" answers on negatives are fixed. Arms: reply mapping declared before `decide`. Never re-score J001 (`HANDOFF:372`).
2. **Negation instruction** (J001 trap 3): a variant TaskSpec; no measurement yet.
3. **Schema variants**: evidence-span field with a verbatim-substring check; rationale before the answer; predicate descriptions; a lab TaskSpec may raise its own max tokens (shipped `JUDGE_MAX_TOKENS` 256, `verify.py:87`).
4. **Lexical ensemble**: composite judge calling pinned `lexical_judge` (specificity 0.960, sensitivity 0.380). A composite that says yes whenever the lexical judge does inherits 0.67 with the model contributing nothing, so the CHOICE declares an ablation comparator (the same composite with the model's reply replaced by a constant "no", or by the record-blind control) and the pass test additionally requires the paired difference to that ablation to have its 95% lower end above 0.
5. **Drafted-terms payload** (D002: drafted 0.590 vs hand-built 0.406, run 38024536763). Terms are drafted only from records outside the draw and received before every drawn record (a received-date split); the term set's sha256 is committed in the CHOICE before the run; in that cycle the lexical comparator of §2.4 is the lexical judge with the same drafted terms (the hand-pack lexical judge reported beside it), and the pass test must beat the drafted lexical judge.
6. **a-4b reasoning on** (`--reasoning on` is allowlisted, `lab/manifest.py:121`; a-4b runs with `--reasoning off`, `lab/models.json`; the warm-up stops on any `reasoning_content`, `lab/warmup.py:44`, so this needs a reviewed lab change). Cost: about 1-2 min per call, 5-10 h per model for 300 calls, spread across parts (up to 30).
7. **R003 settings** (`HANDOFF:329-331`). For J-cycles this reduces to the negation instruction plus pack-v2's merged predicate set (j1 already never asks `unknown_or_other` or a filed predicate's twin). A pack-v2 cycle recomputes the lexical, predicate-only and record-blind comparators on pack-v2 and its result is labelled "pack-v2", never pooled with `pack/` cycles toward 86.
8. **Larger local model rung**: manifest entry plus a check run (`docs/lab/MODELS.md:40`, "Adding a model"); keys only, no family names.
Do **not** build Tesseract into the accuracy track: decisions 1, 2 and 7 are open, and the record-level gain is expected to be small (narratives up to 2,824 characters, cut at 6,000).

### 2.7 Alternative readings the founder can swap in (do not adopt without the founder)
- **(b1) Same small model, central vs federated:** per record the payload is identical (`verify.py:162-168`), so the ratio is 1 by construction; at ranking level it is E2 with `central: self`, synthetic only, "never a result to show buyers".
- **(b2) Larger hosted model as the central reader:** (BA_site − bound) / (BA_hosted − bound) ≥ 0.86; needs the two secrets, a `kind: hosted` entry with `context_tokens` (`lab/hosted.py:1-16`; `docs/lab/REFERENCE.md:208`), the founder lifting `NHTSA_HOSTED` / `MSHA_HOSTED` (`lab/request.py:234, 237`), and a new hosted-central role for j2 (§7). Hosted scores are not deterministic (`REFERENCE.md:530-532`).
- **(c) Detection recall as a share of the pooled national channel (POWER):** model-free, 481 s, pinned `pilot/power.py`; split channels caught 0.22-0.28 of pooled on sextuplings (`docs/collective/POWER.md:80-84`); says nothing about reading.
- **(d) Gate-status agreement with the key:** 5 items in L001 (run 2: 0, 1 and 2 of 5; a constant status gets 3); a count, not an estimate.
- **Site-level BA (L-type):** rejected as primary: route-role scores 0.808 reading nothing.

> **Founder update, 2026-10-11 02:08 UTC, after this prompt was written:** "Run the entire embedded bench ... the entire
> emergence benchmark end-to-end ingestion ... each neural graph takes in the messages, ingests it, goes into the NATS,
> and then goes into the actual fabric. Make sure tesseract routing is used. No mistakes allowed. End-to-end
> benchmarking is a requisite." This supersedes 3.2's order and cut rule: the demonstration load is the
> enterprise-hierarchy benchmark of `research/mycelic/` (`docs/MYCELIC_ENTERPRISE.md`), the Tesseract leg is
> mandatory, and the design brief `docs/collective/E2E-INGEST.md` carries the binding specification.

## 3. Goal B: end-to-end ingestion (NeuralGraph → Tesseract → fabric API → outbox → JetStream → apply) with lineage to source

State at HEAD `abbf2e4`: the four parts exist and are tested separately; the chain exists only in the research routing spike (`research/routing_spike/`), which publishes to an in-process fabric (`publish.py:53-58`, `nats_url=None`, `InProcessTransport()`), is synthetic, and is in no CI workflow. Nothing in `mycelic/collective` publishes into the fabric (`HANDOFF-2026-10-09.md:170-171`). NATS is the fabric's internal log: every event enters through `POST /events`, `POST /memory` or the SDK, is written to the outbox, HMAC-signed (`mycelic/service.py:1041-1044`) and published; a publish that bypasses the API is terminated, but only when the service is keyed (`service.py:929-931`, `if self.keyring.keyed`; `tests/mycelic/test_jetstream.py:541-551`). Raw records and graph nodes never cross the Boundary; only verdicts and conclusions do.

### 3.1 Done-criteria (all synthetic, stamped `measurement: false`; thresholds committed in `docs/collective/E2E-INGEST.md` before any build; E2E-INGEST.md may raise these floors, never lower them)
0. **Real fabric, keyed.** The service runs under `mycelic.harness.ProcessDriver`, which refuses to start without nats-server and sets `MYCELIC_EVENT_SIGNING_KEY` (`mycelic/harness.py:97-99, 111, 138`); the JSON report records `keyring.keyed == true`, `transport == jetstream`, the broker version string read from the live nats-server, and `stream_messages > 0`. A run that publishes through `InProcessTransport` or an unkeyed service fails criterion 1 outright.
1. **Signed.** Every `collective.verdict` and `collective.conclusion` event in JetStream verifies under the keyring; `events_failed{signature}` is 0 before the forgery drill and exactly 1 after one forged publish straight to NATS, whose memory id is absent from the DB. Minimum counts: verdicts ≥ 6 (one per routed site that answered) and conclusions ≥ 1, at least one conclusion per planted candidate, and the planted candidate's conclusion status equal to the spike key's expected status.
2. **Lineage to the source.** 100% of conclusions (count ≥ 1) have `lineage.reconstruct(...)["evidence"]["reconstructable"] == True`; their `source_event_ids` include one verdict event per routed site that answered; each verdict sha256 matches HQ's receive log and the site's egress log; a site-side audit joins sha256 → `verdict_log` → the `record_ref`s read, all on or before `as_of`; a scan of the fabric DB and stream finds 0 record text and 0 `record_ref`.
3. **Idempotent re-ingest.** Re-running site ingest gives the same node ids and count and the same top 10 on ≥ 20 fixed queries per site; re-running the HQ→fabric bridge leaves `stream_messages` unchanged and creates 0 new memory ids.
4. **Recovery.** With N ≥ 50 publishes, nats-server killed after k of them (1 ≤ k ≤ N−10) and restarted: all N applied exactly once, `outbox_pending=0`, `consumer_pending=0`. The service SIGKILLed after at least one conclusion is applied and its DB deleted: the rebuild gives identical active memory ids and the same verify report digest for the conclusions.
5. **Measured numbers** (JSON report uploaded as a CI artifact, with the runner's CPU model): site graph build seconds per 1k records; Tesseract per-question p50/p95 at 600, 5,882 and 20,000 nodes; question→verdict p50/p95; event `created_at`→`applied_at` p50/p95; sustained events/s until apply p95 exceeds 1.0 s (`DEPLOYMENT.md:165-169, 440-446`). Pass condition: apply p95 ≤ 1.0 s at a sustained ≥ 2 events/s for ≥ 60 s (≥ 120 events); the rest are reported and labelled runner-dependent (lab CPUs vary 1.5-3× between EPYC 7763/9V74/9V45 and Xeon 8573C/6973P-C).
6. **CI.** A workflow on this branch (push plus `workflow_dispatch`) runs criteria 0-4 and 7 against a real nats-server v2.10.29 (`mycelic.yml:16`) in under 20 minutes and uploads the report; criterion 5 runs as a separate `workflow_dispatch` job (or a second job in the same workflow) with its own bound of 45 minutes; `mycelic.yml` runs on this branch again and includes `pytest tests/routing_spike`.
7. **Guards untouched.** `tests/mycelic/test_collective_guards.py` and every hash-pin test in rule 4 pass unchanged; HQ-side and bridge modules import none of numpy, `NeuralGraph.llm_backend`, `NeuralGraph.research.retrieval`, enforced like `research/routing_spike/run.py:80-84` (`FORBIDDEN_HQ`, `FORBIDDEN_PUBLISH`).

### 3.2 Build steps (new modules only; order and cut rule)
Order: the shipped-path bridge first (needs no Tesseract and is the part a pilot would use), then the Tesseract leg. One line each; the bodies belong in the S1 design brief, which must fix the demonstration load, N, k, the query count, the planted seed, the one-org layout (the fabric caps 5,000 active notes per org at about 2 notes/s, `DEPLOYMENT.md:165-169`; publish verdicts and conclusions only, never per-record nodes), the report JSON schema and the runner CPU field, and must state that building the option (a) shape (a separate site-side process) does not decide Decision 1.
- **S0 (1 h).** `.github/workflows/mycelic.yml` (not pinned): add `claude/mycelic-production-deployment-r93cez` to `push.branches`, `workflow_dispatch`, `research/**`, `lab/**` and `.github/workflows/**` to the paths filter (`:5-6`), keeping the paths block before `pull_request` and every existing step and env; add `python -m pytest tests/routing_spike -q -p no:cacheprovider`. Run `tests/mycelic/test_packaging.py` and `tests/onboard/test_onboard_demo.py` locally first (they read the file, `:130-131`, `:1361-1365`). Push; watch the run. The branch has had no `mycelic.yml` run since 36515929458 (2026-09-29). A 45-minute CI job now starts on every push that matches the paths, so do not push such code while a lab run's shards are running.
- **S1 (2 h).** `docs/collective/E2E-INGEST.md` committed alone on `wf/e2e-ingest-design`; adversarial review via Workflow; amendments before any build.
- **S3 (3 h).** `research/e2e_ingest/publish_api.py`: the event schema of `research/routing_spike/publish.py`, sent through `mycelic.sdk.MycelicClient.publish_events` against a live service, idempotency keys `{question_id}:{site}:{seq}` and `{conclusion_id}:{version}`; tested with `ProcessDriver` and a real nats-server.
- **S4 (3 h).** `research/e2e_ingest/hq_bridge.py`: read-only reader of the shipped HQ store's `pd_verdicts` / `pd_conclusions` (`mycelic/collective/detect/store.py:127-136`, immutable tables carrying sha256) publishing through S3 with its own watermark. Closes `HANDOFF:170-171` for the shipped lexical path.
- **S6a (2 h).** Bridge fault drills: kill the broker mid-publish, SIGKILL the service, delete and rebuild the DB, run the bridge twice (criteria 1, 3, 4).
- **S8 (2 h).** `.github/workflows/e2e-ingest.yml`: criteria 0-4, 6, 7; artifact upload; nats-server install copied from `mycelic.yml:24-28`.
- **S2 (4 h).** `research/e2e_ingest/site_graph.py`: persistent, incremental SQLite NeuralGraph built from `RecordStore` by a `seq` watermark, upsert keyed on `record_ref`, never deleting the graph; reuse `research/routing_spike/site_process.node_text` and `AsOfStorage` by import. Tests: re-ingest, crash and resume, `as_of` bound.
- **S5 (5 h).** E2E harness: ProcessDriver, 6 site processes in process mode with Tesseract, HQ from `research/routing_spike/hq.py`, then S3; one planted seed of `device_quality`; asserts criteria 1-3.
- **S6b (1 h).** Restart a site mid-question (criterion 4's site half).
- **S7 (3 h).** Per-hop measurement at three graph sizes writing the JSON report (about 30 min wall time; its own CI job).
- **S9 (4 h).** Build review, fixes, one recorded run of each job, `## Runs` in E2E-INGEST.md and the CI record in `docs/lab/INTEGRATION.md`; handoff status lines only.
Cut rule: if by hour 24 fewer than one J-cycle is recorded, S2, S5, S6b and S7 move to the next 48 h and Goal B is reported as "bridge criteria 0, 1, 3, 4, 6, 7 met or not; Tesseract leg (2, 5) not reached".
Local tooling: nats-server is at `/root/.local/bin/nats-server`, v2.10.22; CI runs v2.10.29 and is the reference. Docker is not available in the sandbox. If a download through the configured proxy is refused, do not work around it: run those tests on the runner through the new workflow.

## 4. Start-of-loop checklist (hour 0, and again after any container restart)

```
cd /home/user/NeuralGraph
git fetch origin --prune
git checkout claude/mycelic-production-deployment-r93cez && git pull --ff-only
git status --porcelain            # must be clean; if not, inspect before anything else
git rev-list --left-right --count origin/claude/mycelic-production-deployment-r93cez...HEAD
git log --oneline -12
git branch --list 'wf/*' ; git branch -r --list 'origin/wf/*'
ls lab/requests/
sed -n '/^## Runs/,$p' docs/collective/L001/CHOICE-L001.md | grep -n '^### Run'
grep -n '^## Run' docs/lab/RESULTS.md | tail -3
gh api 'repos/anovruzov/NeuralGraph/actions/workflows/mycelic-lab.yml/runs?per_page=5' \
  --jq '.workflow_runs[] | "\(.id) \(.head_sha[0:7]) \(.status) \(.conclusion) \(.created_at)"'
gh api 'repos/anovruzov/NeuralGraph/actions/runs?per_page=10' \
  --jq '.workflow_runs[] | "\(.id) \(.name) \(.head_sha[0:7]) \(.status) \(.conclusion) \(.created_at)"'
tail -40 docs/handoff/LOOP-48H-STATUS.md 2>/dev/null
```
State as of 2026-10-11 01:45 UTC, for orientation: HEAD `abbf2e4`, 0 ahead / 0 behind; K14 merged (`3fd230f`) and pushed; probe run 38102272696 passed; `lab/requests/latency-002.json` present; **L001 run 2 = lab run 38102412415 completed with success at 01:44:20 UTC and is unrecorded** (RESULTS.md ends at run 9, CHOICE-L001 Runs holds run 1 only). The local `wf/l001-k14*` branches are ancestors of the pushed HEAD and need no push; `749cae6` is an orphan WIP superseded by `c6b5ab0`.
Decide where you are:
- **A lab run completed and not recorded in RESULTS.md:** record it first (§8). At hour 0 this is L001 run 2.
- **A lab run in progress** (`lab/requests/<x>.json` pushed, run `in_progress`): set §6.2's check-ins and work the other track.
- **Nothing in flight:** the next stage of each track per §5.
- **`## Decisions received` in LOOP-48H-STATUS.md holds a call on decision 9:** the J-cycle rule adopts it; otherwise the shipped `decide` is primary and arms are declared secondaries.
- **A `wf/` branch exists only locally and is not an ancestor of origin's HEAD:** push it before anything else (a push that touches no `lab/requests/*.json` starts no lab run).
Create `docs/handoff/LOOP-48H-STATUS.md` if absent (new, unpinned): the loop's start time, hour, state, `## Open asks` with the date each was asked, `## Decisions received`, and later the final report. Then read `docs/lab/RESULTS.md` (all run sections), `docs/collective/replay/vehicles/CHOICE-J001.md` (rule and Runs), `docs/collective/L001/CHOICE-L001.md` (Runs, K10, K14) and `BUILD-L001.md` (`:479-491` as built), `docs/lab/REFERENCE.md` (j1 table, limits), `lab/requests/README.md`, `docs/handoff/HANDOFF-2026-10-09.md`, `docs/strategy/YC-BRIEF.md`.

## 5. Schedule (48 h; one lab run in flight at a time; the lab caps `max_parallel` at 16, `lab/request.py:871`)

| Hour | Accuracy track (foreground) | Ingestion track (Workflow, parallel) | Runners |
|---|---|---|---|
| 0-1 | Checklist. Record L001 run 2 (§8: RESULTS run 10, CHOICE-L001 `### Run 2`, handoff status line). Create LOOP-48H-STATUS.md with §7's asks. Commit, push | S0: restore CI on this branch; watch one run (+10, +25 min) | `mycelic.yml` |
| 1-8 | J2: rule alone on `wf/j2-design` → Workflow adversarial review → amendments → build on `wf/j2` → build review → fix on `wf/j2-fix` → merge (tests/lab and the L001 dry run on the merged tree) → push code without a request → push `lab/requests/judge-002.json` alone. J001's build-to-merge was 2 h 51 min and L001's rule-to-merge 5 h 12 min with a similar wiring footprint | S1 design committed alone → review → amend; start S3, S4 | J2 (judge-sized: J001 took 21.5 min; more arms or parts, longer) |
| 8-14 | Prepare J3's rule (next lever) while J2 runs; record J2 when the aggregate job ends (+5, +15, +30 min check-ins) | S6a drills, S8 workflow, first `e2e-ingest.yml` run | J2, then `e2e-ingest.yml` |
| 14-19 | J3 build/review/fix. Buffer: every lab experiment so far needed one re-run, re-aggregation or amendment cycle | fix what the first CI run failed (synthetic, `measurement: false`: re-runs are allowed here) | re-run if needed |
| 19-24 | Push J3's request; record it | S2 site graph (if the cut rule allows) | J3 |
| 24-32 | J4 rule and build; push its request; record J3 | S5 harness, S6b, S7 measurement job | J4 |
| 32-40 | J5 only if J4 is recorded by hour 33; otherwise second buffer | S9 build review, fixes, one recorded run of each job, E2E-INGEST.md Runs, INTEGRATION.md | J4, J5 |
| 40-44 | Record J4 (J5). Update RESULTS, CHOICE Runs with the comparison count, YC brief (measured numbers only), handoff status lines (never the decisions column) | INTEGRATION note | — |
| 44-48 | Suites on the final tree, pushes, final report (§9) in LOOP-48H-STATUS.md. Nothing new is pre-registered in the last 4 h | | — |

Expected output: L001 run 2 recorded plus 2 J-cycles plus the shipped-path bridge; a third J-cycle and the Tesseract leg only if nothing needs a second cycle. The history supports about one recorded result per 9-10 h with everything else included (D002 2 h 01 min at best, R002 8 h 38 min), and both tracks share one 4-vCPU sandbox and one session's attention.

## 6. Loop mechanics

### 6.1 Workflow
Use the Workflow tool for every substantive stage: adversarial review of a rule (read-only agent, writes findings as numbered blocking/minor items), build, build review, fault drills, recording. Each Workflow ends in a pushed commit on a `wf/` branch. Reviews are adversarial: they look for ways the rule can pass without the model reading (structure, leakage of the key, draw bias, a draw that is not fresh), for guard collisions like L001 run 1's (a refused value equal to a shard label, run 38090725021), and for anything that would make the first run not the result.

### 6.2 Self check-ins with `send_later` (set them the moment you push)
- Any lab request push: +5 min (plan job: guard, prereg refusal, empty draw, settings), +15 min (how many shards running).
- Judge-sized run: +30 min (J001 21.5 min; L001 run 2 7 min 38 s). Reader/sim-sized: +90, +180, +300 min; hard stop +330 min (325-min shard timeout plus overhead; `lab/request.py:174` `SHARD_OVERHEAD_MINUTES` 25).
- Probe workflows: +3 min, hard wait +10 min. Onboard workflow push: +2, +20 min. CI (`mycelic.yml`, `e2e-ingest.yml`): +10, +25 min.
- Background suite in the sandbox: +25 min.
- Standing heartbeat every 2 h: re-run §4's state commands, append one status line to LOOP-48H-STATUS.md, commit, push.
Reading runs: `gh api 'repos/anovruzov/NeuralGraph/actions/runs/<id>/jobs?per_page=30' --jq '.jobs[] | "\(.name) \(.status) \(.conclusion) \(.started_at) \(.completed_at)"'`. The sandbox `gh api` refuses the log and artifact redirects; the GitHub MCP tool `get_job_logs` (`return_content: true`, `tail_lines: 700`) does return a job's tail, saved to a file when large. The lab prints each report between markers near the end of the aggregate job's log (`lab/summary.py:95-99`; `docs/lab/README.md:195-205`): `=== MYCELIC-LAB <label> BEGIN lines=<n> sha256=<hex> ===` … `=== MYCELIC-LAB <label> END ===`. Copy the lines between the markers; for `report.md` hash the UTF-8 bytes, for `report.json` re-serialise as canonical JSON (sorted keys, no spaces, final newline) and hash; the hash must equal the BEGIN line's; record its first 12 hex and "matching" in RESULTS, as `RESULTS.md:318-319` does.

### 6.3 When a lab run fails
- **Guard, prereg refusal, empty draw or settings stop before any model call** (L001 run 1's case): not a result. Write it into the CHOICE `## Runs` as "stopped before any model ran: run <id>, <reason>", fix the lab-side cause as a numbered amendment (review before build), rebuild, push code without a request, then the request alone, **with the same seed and n**. A seed change is a numbered amendment that states a cause independent of the printed figures and goes through review. The plan job prints the lexical, record-blind and predicate-only figures before any shard starts (`lab/prereg.py:318-331`): those figures are never a reason to re-draw, and a cancellation by you after the plan job has printed is recorded in CHOICE Runs with the figures you had seen.
- **Runner or network failure before any model judged** (provision download, cache miss, runner lost): record it in CHOICE Runs and re-push the same request unchanged once, as K10 and rule 13 allow (`CHOICE-L001.md:727-730, 860-861`).
- **After model calls started** (shard timeouts, aggregate scoring nothing, a shard failure): the run **is** the result for whatever finished. Declare any re-aggregation in the CHOICE Runs section before running it (budget 40 min, as R002 did: `f8653d9` then `e36a903`). Never re-run the same rule. The next attempt needs a new rule on a fresh draw.
- **CI, probe or onboard workflow failures** with no CHOICE file behind them are ordinary bugs: fix, re-run.
- **Container restart:** run §4; recover only from pushed commits; re-run interrupted suites.

### 6.4 Pushing and merging
- Code branch and request file go in **separate pushes**: the request push is the only thing that starts a lab run (`mycelic-lab.yml:3-7`).
- Merge each `wf/` branch into `claude/mycelic-production-deployment-r93cez` with `git merge --no-ff`, run the relevant suites on the merged tree, push with `git push -u origin claude/mycelic-production-deployment-r93cez`.
- Never `main`, never a PR, unless the founder says so.

## 7. What needs a human, and how you ask

Write each ask as one line prefixed `NEEDS FOUNDER:` or `NEEDS CHIEF SCIENTIST:` in your reply and under `## Open asks` in LOOP-48H-STATUS.md with the date. Ask once. Never assume the answer. Never block the loop on it; continue with the parts not gated.
- **Founder, hour 0:** "NEEDS FOUNDER: 86 is read as balanced accuracy 0.860 of the site verifier against the filed-category key, record level, codes hidden, fresh real NHTSA draw (§2.1-2.5); confirm or name one of §2.7. The loop runs §2 by default and will not switch after a result." A correction before the J2 rule commit changes the rule; after it, the next cycle.
- **Founder, main/PRs:** "NEEDS FOUNDER: may the loop open a PR / push to main? Default is no."
- **Founder, hosted model:** the secrets `MYCELIC_LAB_HOSTED_BASE_URL` and `MYCELIC_LAB_HOSTED_API_KEY`, a `kind: hosted` entry in `lab/models.json` with `context_tokens`, **and** whether to lift `NHTSA_HOSTED` / `MSHA_HOSTED` (`lab/request.py:234, 237`). These are necessary, not sufficient: `J1_KINDS_PROBLEM` (`:236`) refuses a hosted model in j1 and `HOSTED_PLACE` (`:232-233`; `lab/hosted.py:5-7`) allows a hosted model only as an E1 endpoint or reference or as `e2.central`, and the workflow passes the secrets to hosted shards only. After a yes, the loop builds and reviews a hosted-central role for j2 (request validation, `plan.filter_hosted`, units, shard secret routing, warm-up; the secrets reach only that shard), about one review cycle. The standing rule forbids any other model-host egress; do not look for one.
- **Founder, docs:** the STRATEGY 9.1 ruling on the fictional demo (`demo/collective/README.md:9-12`); publishing the docs-honesty fix for `site/index.html:289` (the audit reads lexically; `HANDOFF:346-348`, item 5.1). Prepare the text; do not publish.
- **Chief scientist, decision 9** (how "unclear" counts) before the J2 rule commit. Default if silent: shipped `decide` primary, declared mappings secondary.
- **Chief scientist, R003 settings** (`HANDOFF:329-331`) before any lever-7 cycle.
- **Chief scientist, decisions 1, 2, 7** (Tesseract placement, routing, embedder). The ingestion track builds the option (a) shape and states that no decision was made.
- **Chief scientist, the chance null** (plan 1.1, `HANDOFF:259-268`) and detector settings per site count (1.4-1.5): not gating this loop, gating the pilot's meaning.

## 8. What to record, where
- `docs/lab/RESULTS.md`: one `## Run N: <request> (<what>): <verdict>` section per lab run, in the file's existing format (run id link, request file, commit, UTC times, rule file and amendments, report sha "matching", table, the pre-registered verdict). Next number is 10 (L001 run 2, lab run 38102412415, request `latency-002.json`, commit `abbf2e4`, 2026-10-11 01:36 to 01:44 UTC, aggregate job 114362048048, `report.md` sha256 `e8b3960786ca…`, verify it yourself).
- Each CHOICE file's `## Runs`: run id, times, the verdict by the pre-registered rule, the running comparison count (J-cycles), any re-aggregation declared before it ran, anything that stopped before a model ran.
- BUILD notes: "as built" counts, durations, mutants, dry-run times.
- `docs/collective/E2E-INGEST.md`: thresholds (committed alone) then `## Runs` with the CI run id and the JSON report's numbers; `docs/lab/INTEGRATION.md`: the CI run record.
- `docs/strategy/YC-BRIEF.md`: only measured numbers with run ids; never a projection or a post-hoc reading.
- `docs/handoff/HANDOFF-2026-10-09.md`: status lines and plan ticks; the decisions table stays as it is.
- `docs/handoff/LOOP-48H-STATUS.md`: hourly lines, open asks, decisions received, the final report.

## 9. Final report at 48 h (and at every later 48 h mark while looping)

```
# 48-hour loop report, <UTC start> to <UTC end>, HEAD <sha>

| Goal | Pinned definition | Measured result (point [95% interval]) | Comparators on the same records | Verdict (success / provisional / partial / failure / incomplete) | Source (run id, file:line) |
|---|---|---|---|---|---|
| A: 86 accuracy | BA vs filed-category key, fresh NHTSA draw, codes hidden (§2); primary pair per cycle; comparisons made so far: <count> | per model and arm | lexical, predicate-only bound, record-blind | | |
| B: ingestion | criteria 0-7 (§3.1) | each criterion pass/fail; the five measured numbers with the runner CPU | — | | |

Row note for Goal A: the key's noise ceiling is unmeasured and the models may have seen these public complaints in training; if 86 was reached, say it was on public text the models may have seen.

## Done (merged, pushed): commits, branches, runs
## Failed or not reached: the number, the gap to 0.860, what the interval says, the reason as measured
## Stopped before any model ran: run ids and causes
## Open asks (founder / chief scientist), with the date each was asked, and decisions received
## Next pre-registered lever (name, draw, primary pair, arms), and the next ingestion step
## Not found: anything you looked for and could not find
```
Every number carries its run id or `file:line`. Nothing is rounded toward the target. If 86 was not reached, the first line under Goal A reads: "Not reached. Best measured BA b [lo, hi] on primary pair P; gap to 0.860 is g." Then keep looping (§0).

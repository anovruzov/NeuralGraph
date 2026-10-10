# L001 build note: the lab's `l1` experiment, as built

What was built so the lab can run latency test L001 (`CHOICE-L001.md`, with its section "Amended before any run", K1
to K13). The choice file is the rule; this note says how the build carries it out, where the rule left a choice to the
build, and what the dry run showed. The rule was committed alone (`0fa028d`) and amended before any code (`9f505e7`);
this build starts from `9f505e7` on branch `wf/l001`, and its fixes after review are on `wf/l001-fix`, from `9531318`
("Fixes after review"). While building, no value of MSHA's file was read: the sandbox reaches no MSHA host, so every
run here read the synthetic file of `tests/lab/l1_data.py`, whose every value is invented. No model ran: every server
here was the lab's fake.

## The shape

| Step | Where | What |
|---|---|---|
| Request | `lab/request.py` | an `experiments.l1` block: `models`, `minutes`, `questions` (exactly 5), `bootstrap_b`, `bootstrap_seed` |
| Plan | `lab/plan.py` | one unit per model and question slot, `l1-<model>-q<k>`; slot 1 is the alert question |
| Preregistration | `lab/prereg.py` runs `python -m lab.l1 prereg` (plan job) | the file, the data, the questions, every comparator on its own fresh stores, the scores, the draws, the warm-up payloads |
| The file's cache | `lab/msha.py cache-key` and the workflow | the plan job saves `lab-msha` under `lab-msha-<16 hex of the sha256>`; the run and aggregate jobs restore it |
| Unit | `lab/units.py` runs `python -m lab.l1 run` | one model on one question, one runtime per mine at `site:<mine>` |
| Warm-up | `lab/warmup.py` | `judge_record` with the preregistration's generated payloads (K5) |
| Guard | `lab/msha.py guard` over `lab/l1guard.py` | every file of the plan directory, each shard root and the report directory |
| Aggregate | `lab/aggregate.py` | an `l1` block: per model, the scores, D_lex, D_route, the headline (K3), the strata, the gate, the per-record measures, the latency figures; the re-run rule (K10) |
| Summary | `lab/summary.py` | the L1 sections of the plan and report summaries |
| Notes | `lab/notes.py` | the fixed sentences, columns and headings (no ASCII digit) |
| Template | `lab/templates/latency.json` | L001's settings, to copy to `lab/requests/latency-001.json` |

New modules: `lab/l1.py` (pins, the plan job's step, the unit), `lab/l1path.py` (the data and one judge's path on one
question), `lab/l1score.py` (scoring, pure), `lab/l1guard.py` (the guard) and `lab/msha.py` (the workflow's two L1
steps). The request file is not added under `lab/requests/`: pushing a file there to any branch but `main` starts a
run (`lab/requests/README.md`), and the caller starts the run. The template's purpose names
`lab/requests/latency-001.json`, as the lab's other templates name their copies.

No pinned module was changed: nothing under `mycelic/collective/` at the top level, `detect/`, `edge/`, `packs/*.py`,
`evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/` or `connectors/openfda.py`; nothing under
`mycelic/collective/onboard/`, `pilot/audit.py`, `pilot/power.py` or `pilot/start.py`; no `CHOICE-*`, `run-*.json` or
`*-settings.json` of an earlier test; nothing under `docs/collective/replay/vehicles/pack/`. The drafter demo
(`demo/onboard/run_demo.py`) and `tools/onboard/fetch_msha.py` are imported as modules, read-only.

## How each rule is carried out

- **Rule 1, the data.** `l1path.prepare` runs the demo's steps (a) and (b) (`step_export`, `step_draft`) with D002's
  settings, normalises c1's held-out years, replaces each mine id by its label (`mine_labels`), and runs the pilot
  audit. Every path's pipeline is built from the audit's own records, weeks, sites and master data
  (`l1path.audit_inputs`, the audit's preamble), so it is the audit's pipeline. The plan job records the file's sha256
  beside the demo's recorded input, the pack's hashes and the audit's counts; when the file is the demo's, the counts
  must be the demo's figures (stop `demo_figures`). Every unit rebuilds the data and checks the config hash, the audit's
  counts, the alert list and the mines before any call.
- **Rule 2 and K11, the alert.** Exactly one X or S alert in the evaluated weeks, else the plan stops (`no_alert`,
  `alerts`). The alert path runs HQ's detection at the alert's available date in its channel (tie salt `pilot`), saves
  it, and checks that the run lists the alert in its week with its snapshot (`alert_run`).
- **Rule 3 and K11, the questions.** Slot 1 asks `verify_stored`; slots 2 to 5 are the other four specific predicates,
  sorted, each a `constructed_candidate` with the alert question's window start, then `verify_candidate`. Not exactly
  four others stops the plan (`questions`).
- **K4, fresh stores.** Every path (one judge on one question) builds a new pipeline in a new directory, so new HQ and
  mine stores. The plan job runs a probe path per question first (its handlers answer nothing), so each question's id,
  window and routes are known; every judge's path must give the same id and routes (`routes`). The unit's timed model
  path runs on stores no other judge touched, and its lexical rerun after it.
- **Rule 4, the mines.** Every judge but the key reads a copy of each mine's records with `codes` emptied
  (`l1path.hidden_sites`); the plan job and each unit check that the copy retrieves the same records, in the same
  order, as the unchanged store (`retrieval`). Each mine's verifier is `SiteVerifier` with `demo_seed` 1 and its clock
  at the question's `as_of` (that day at 12:00 UTC). A model is one `Runtime` per mine at boundary `site:<mine>`, data
  label `public`, its one endpoint the shard's server with HTTP deadline 600 s and 1 retry, `judge_record` routed to it
  with no escalation; the unit refuses any other routing before its first call. Delivery is the orchestrator's own with
  `deadline_seconds` 3600.
- **Rule 5 and K2, the judges.** The key judge, the record-blind control and the route-role baseline are lab runtimes
  with the runtime's surface (the pinned fake provider is simulated-only and refuses real records); the lexical judge
  is `SiteVerifier` with no runtime. The four run in the plan job, each on its own fresh stores, and their verdicts,
  gate statuses and scores are preregistered; the plan job also counts the strata and the construction counts.
- **Rule 6, K5 and K6, the timing.** The clock starts at the call to `verify_stored` or `verify_candidate`: a control
  question's candidate (`constructed_candidate`, an HQ cell query) is built before it (`l1path.question_call`). A
  wrapper around each mine's handler records its start and end (`time.perf_counter`); the stages are the question
  build, each mine, the gaps and the gate, and they sum to the time to answer. A mine still running at its deadline
  ends at its start plus the deadline, and a mine timed while an earlier mine of the question still ran is contended.
  A contended mine's time and a contended question's time to answer are reported apart (`mine_s_contended`,
  `time_to_answer_s_contended`); the question build, the gaps, the gate, the judge calls and the model calls pool a
  contended question's parts with the others, and the latency block names them (`pooled_with_contended`), as the time
  note does. Each mine's first call's latency is beside its median. The derived times (at once, the default deadline
  of 600 s, no model) are computed, never run.
- **Rule 7, K6 and K8, the correctness.** A site answer is scored at the first decision against the key's verdict;
  `unknown` is never correct; an answer with a transport failure in any call is left out for that model and counted,
  and more than 5% left out gives no verdict. The gate's decision is compared at the first decision, beside the
  agreement of a constant status.
- **Rule 8, K3 and K7, the intervals and the headline.** A percentile bootstrap over mines with `random.Random("l1:1")`
  and B 10,000, every judge on the same draws; a draw without a key confirm or a key refute is left out and counted,
  and above 5% of the draws the interval is withheld. The plan job stops when the key's own share is above 5%
  (`draws`). The headline follows K3: `better`, `better_than_lexical_only`, `worse`, `not_told_apart` or `no_verdict`,
  with its reason when it is `no_verdict`: one of the model's units did not finish (`L1_INCOMPLETE`), too many answers
  left out (`L1_LEFT_OUT`) or an interval withheld (`L1_WITHHELD`). A complete model that is not a model measurement
  (a dry run's fake server) has a null headline with `L1_NOT_MEASURED`: L001 does not read it.
- **Rule 10, beside the headline.** Every judge's scores and strata, the predicate-only bound, each question's verdicts,
  reasons, support buckets and statuses, the per-record measures (from each unit's `records.jsonl`, seed
  `l1:1:records`), each counted confirm's share of filed records (`resolve`), the crossing overlap (`leakage.scan`) and
  every latency figure with the CPU models.
- **Rule 11, K10 and K13, the units.** 15 units, one per shard, 300 minutes each, `job_minutes` 330 (timeout 325),
  `max_parallel` 15: the planner gives exactly that for the template. A unit's budget stops the timed path 240 s before
  its end (`SIGALRM`), so the rest and the final `run.json` are written. The checks before the path count against the
  budget, but the timer is armed only when the timed path starts, for the time the budget's clock says is left then
  (none left stops the path at once). A path not finished is reported with its elapsed time as a lower bound: in its
  `run.json`, in its part of the report's `l1` block with its model calls (`elapsed_lower_bound_s`, `model_calls`), in
  the latency block's `not_finished_lower_bounds`, and in report.md's time section, a row per question not finished.
- **Rule 12, K9 and K12, what is written.** Mines are `m01` onwards, records their index in their mine's retrieved
  list, the operator `c1`; the drafted pack is in no artifact, only its hashes. Every number L1's code writes is
  seconds or a share with at most 3 decimals or a count, sizes in KiB. Every step that reads MSHA's file sends its
  output to captured files or prints fixed lines, and an error prints as its class and code location only.

## Choices the rule left to the build

- **How a shard job re-runs, and how the aggregate takes the re-run (K10).** A shard job is re-run with GitHub's
  "Re-run failed jobs" on the same workflow run: the plan, its preregistration and the cache key are the same, and the
  re-run's artifact is a new attempt. The aggregate takes a shard's highest attempt, as for every experiment. For an L1
  unit whose chosen attempt is above 1, it looks at every earlier attempt's artifact of that shard: when one shows a
  model call of the unit (its `run.json` says `model_calls` above zero or cannot be read, a ledger has any row, or the
  unit record counts ledger rows), the re-run is not taken: the unit is `excluded` with `L1_RERUN_AFTER_CALLS` and its
  model gets no verdict. An earlier attempt of which no artifact of the shard was found is read as calls unknown, and
  the re-run is not taken either (`L1_RERUN_CALLS_UNKNOWN`, the attempts in `rerun_calls_unknown`): the upload step
  runs under `!cancelled()`, so a cancelled or timed-out job, the likeliest to have made calls, uploads nothing. So L1
  shards are re-run only with "Re-run failed jobs", under which every attempt runs every failed shard (docs/lab/README,
  "Cancel or re-run"); a shard job that failed before its unit wrote anything (checkout, Python) leaves no artifact and
  costs its model the verdict too. A unit that fails before its first call for its file says so with exit 3 and
  `L1_INFRA`, and its shard's artifact shows no call, so its re-run is taken.
- **Where the guard runs (K9).** In the plan job after the preregistration and again after the summary; in the run
  job after the run step, after the seal and after the summary; in the aggregate job after the aggregate and after the
  summary. So every directory is scanned before a summary reads it and every file, the summaries' own included, before
  upload. A plan without L1 units makes the guard print one fixed line and do nothing. The guard reads the file from
  the cache path; without the file it withholds every file it scans.
- **The file's cache key (K10)** is `lab-msha-` and the first 16 hex digits of the file's sha256, from the verified
  preregistration (`lab.msha cache-key`), saved by the plan job under `${{ runner.temp }}/lab-msha` and restored, with
  `continue-on-error`, by the run and aggregate jobs.
- **The warm-up's payload (K5)** is a generated record (`e3_latency.filler`, seed `l1:warmup`) asked about the drafted
  pack's other bucket, which no question asks, typical and cut at `max_input_chars`; it is preregistered in
  `prereg/l1/warmup.json`.
- **When the unit ends (K4).** The unit check counts each mine's calls after the late wait. A call still in flight when
  the unit stops the path (its runtimes then close) ends without its ledger row, so it is no judgement of the unit: it
  is left out of the ledger check, the failure counts and the per-record replies, and fewer calls than records are
  allowed at a mine that timed out only when its late thread was still running then. K4's breaker allowance is read
  as the verifier makes its stop (`l1.breaker_stopped`): the mine's last `BREAKER_AFTER` (2, `edge/extract.py`) calls,
  in record order, each ended (its last ledger attempt) in a `SERVER_DOWN_KINDS` failure on the route's endpoint. A
  call that failed its attempt and its repair is not a breaker stop.
- **Re-aggregation.** `lab-reaggregate.yml` has no guard over MSHA's file, so a re-aggregation reads no L1 block: the
  block holds only `L1_NO_REAGGREGATION`. The run's own report holds the result.
- **The lab's generic tables.** L1's ledgers give no row to the report's `latency` table, whose figures are
  milliseconds (K12); the `l1` block holds every L1 latency in seconds.

## Deviations

None. No part of the build departs from the rule as amended, so no amendment was needed. The items above are the
choices K10 and the rule's silences leave to the build, not changes to it.

Two changes to the lab's shared code came with the build, each tested and neither changing an earlier experiment's
result: the participation check keys a call by its ledger's boundary as well as its ref (L1's mines number their calls
alike; J1's calls all share one boundary, so J1 reads as before), and `lab.prereg`'s line adds ` l1 yes` only for a
plan with L1 units, so every earlier plan prints the same line.

Existing lab tests were extended where they list the lab's own parts, never weakened: the workflow's command list
gains `lab.msha cache-key` and `lab.msha guard`, the run job's cache-path test selects the restores under
`lab-cache/` (L1's file has its own, tested in `test_lab_l1.py`), the two template lists gain `latency.json`, and
`test_lab_sim.py`'s message for an empty `experiments` block gains `, l1` at its end, as it gained `, j1` with J001
(`l1` is appended to `request.EXPERIMENTS`, so every other message is unchanged).

## Fixes after review (`wf/l001-fix`)

A review of `9531318` found no blocking finding and eight minor ones. Each is fixed on `wf/l001-fix`, with a test that
fails on `9531318`; none needed the rule to change, and CHOICE-L001 is untouched.

1. **The budget test depended on the machine's load.** The unit armed `SIGALRM` before `P.prepare`, so under load the
   timer could fire before the timed path started and the path made no call. Now `_Signals` installs its handlers at
   once and arms the timer when the timed path starts (`enter`), for the time the budget's clock says is left then;
   none left stops the path at once. The unit's behaviour is the same: the checks before the path still count against
   the budget. `RunTests.test_it_stops_at_its_budget` holds the budget's clock still, gives the path 20 s after
   `AFTER_PATH_S`, and checks that the timer is armed once, for those 20 s, after `prepare`, that the path made calls
   and stopped before its last, and that its elapsed time is at least the 20 s. On `9531318` it fails: the timer is
   armed before `prepare`. With four busy processes beside it, it passed (`Ran 1 test in 36.663s`).
2. **The elapsed lower bound stayed in the shard.** A unit whose verified run did not finish now keeps
   `timing.elapsed_lower_bound_s` and `model_calls` in its part of the report's `l1` block (`elapsed_lower_bound_s`,
   `model_calls`, problem `not_finished`, where it read `verdicts_differ` before), the latency block lists them by slot
   (`not_finished_lower_bounds`), and report.md's time section has a row per such question under the column "elapsed
   s, a lower bound", beside a "questions not finished" column in the time table. Test:
   `AggregateTests.test_a_question_not_finished_keeps_its_lower_bound`, with
   `test_a_question_whose_run_is_not_this_one_shows_no_lower_bound`.
3. **A re-run after an attempt that left no artifact was taken (K10).** Such an attempt is now read as calls unknown,
   and the re-run is not taken (`L1_RERUN_CALLS_UNKNOWN`, `rerun_calls_unknown`); see "Choices the rule left to the
   build". Test: `AggregateTests.test_a_re_run_after_an_attempt_without_an_artifact_is_not_taken` (attempt 1 missing,
   then attempt 2 missing between found attempts 1 and 3).
4. **A control question's time included building its candidate.** `l1path.question_call` builds the constructed
   candidate before the clock starts, so rule 6's time runs from the call to `verify_candidate`. The alert question's
   time is as before. Test: `PathTests.test_a_control_question_is_timed_from_its_call` (a candidate build slowed in
   the test ends before the clock's start).
5. **K4's breaker allowance was any two failed rows.** It is now the verifier's own stop (`l1.breaker_stopped`): the
   last `BREAKER_AFTER` calls in record order each ended in a `SERVER_DOWN_KINDS` failure on the endpoint. Test:
   `RunTests.test_the_unit_check_s_breaker_is_the_verifier_s` (a call whose attempt and repair failed, two downs then
   a call, a validation failure between, another endpoint, one down call: each fails the check; two downs at the end,
   or a repair that ended in a timeout, pass it).
6. **The time to answer pooled contended questions (K6).** `time_to_answer_s` is now over questions without a contended
   mine, and `time_to_answer_s_contended` over those with one; `pooled_with_contended` names the figures that still
   pool them (question build, gaps, gate, judge calls, model calls), and the time note says so. Test:
   `ScoringTests.test_the_latency_figures`.
7. **An unfinished model's headline was null.** It is now `no_verdict` with `L1_INCOMPLETE`, K3's "no verdict", whose
   text now says "no verdict". A complete model that is not a model measurement keeps a null headline with
   `L1_NOT_MEASURED`. Tests: `ScoringTests.test_the_headline_at_its_boundaries` and
   `AggregateTests.test_a_missing_question_leaves_a_partial_reading`.
8. **Clauses no test pinned.** New tests: `AggregateTests.test_the_first_decision_is_scored` (a timed-out mine whose
   late verdict and final status differ from the first ones),
   `RunTests.test_a_lexical_rerun_that_differs_fails_the_unit`,
   `PreregTests.test_a_judge_s_other_routes_stop_the_plan` (other routes, and another question id),
   `GuardTests.test_the_server_s_logs_are_scanned`, `AggregateTests.test_a_re_run_after_ledger_rows_is_not_taken`
   (no model call in run.json or the unit record, a ledger with rows) and
   `ScoringTests.test_an_interval_at_exactly_one_in_twenty_empty_draws`. The review's eighth survivor, a unit without
   the probe's routes pin, is equivalent while the later check compares the routes, so it has no test of its own.

Each of the review's seven survivors, and a mutation undoing each fix above (the timer armed at once, any two failed
rows as a breaker stop, missing attempts ignored, the clock started before the candidate, contended questions pooled,
a null headline for an unfinished model, the lower bound not copied, its summary table left out), was applied to a
copy of this tree, one at a time; the tests named above failed under each of the fifteen.

## Known limits of the build

- The guard's values come from D002's split of the file, c1 to c5 only; other operators' values are kept out by K9's
  console rule, not by the scan.
- The lab's shared unit record writes the participation share with up to 6 decimals, as for every experiment; K12
  covers the numbers L1's own code writes.
- The plan job's L1 step must finish within its 18-minute subprocess limit and the job's 30 minutes, and the run
  job's two guards after the run step within the job's last 10 minutes; on the real file these times are not yet
  measured.
- `actionlint` is not installed here; the workflow is checked by `tests/lab/test_lab_workflow.py` and
  `tests/lab/test_lab_l1.py` only.

## Tests

`tests/lab/test_lab_l1.py`, offline: the synthetic file (`tests/lab/l1_data.py`), the lab's fake server (a responder
with no pack: `lab.responder.pack_free_judge`), and no network host (every fetch is stubbed). One dry run of the
template with one model is shared.

- Rules 1 to 3 and K11: the demo's steps on the file, the mines as labels, one alert and five slots, the stop when
  there are not exactly four other predicates.
- Rules 4 to 6, K1, K2, K4 and K6: the codes hidden from every judge but the key, the retrieval check, each question
  routed from its own key, fresh stores for every path, the judges' rules, one mine after another with the stages
  summing to the time to answer, a control question timed from its call, a mine that times out (unknown at the
  first decision, contended next mine, late verdict, the derived default deadline), the stages by hand.
- Rules 7 to 10, K3, K7 and K8: site answers and what is scored, unknown never correct, rule 8's draws, a mine's
  answers drawn together, withheld intervals, the paired differences, the predicate-only bound by hand, the headline at
  every boundary, the gate's agreement beside a constant status, the strata and the construction counts, the
  per-record measures, the latency figures (a contended question's time to answer apart, the lower bounds of the
  questions not finished), an interval at exactly one in twenty empty draws.
- The preregistration: its files and keys, the route-role baseline and the lexical judge by construction (K2), the
  manifest's block and the cache key, every stop before a path and after the paths (a judge's other routes or
  question id among them), the plan job's reading of a stop and of a failed fetch.
- A unit through the fake server: one call per retrieved record and a ledger row for each at its own boundary, the
  timing and K12's number forms, every pin checked before any call, the file checked before any call (exit 3), the
  budget (its timer armed when the path starts), transport failures counted and recorded by index, a lexical rerun
  that differs, the unit check and the verifier's breaker stop, the recorder after the unit stopped the path, an error
  printed as its class and place only.
- The lab's wiring: argv, one routing file per mine, the status reading, the participation key, the warm-up (K5).
- The dry run: the plan and units, the report block, the summaries' every number traced to its file, and no record
  id, mine id, name, id or 8-word narrative run anywhere a run writes.
- The aggregate: the headline for a complete measurement, a missing question, the first decision scored, a question
  not finished with its lower bound, a changed records file, a run of another question, transport failures above one
  in twenty, a re-run after a model call, after ledger rows, after an attempt that left no artifact, and before any
  model call, no preregistration, re-aggregation.
- The guard: its values, a hit withholds the file and fails the step with counts only, the server's logs scanned, no
  file withholds every file, other plans left alone, an error's class and place.
- The workflow (K9, K10), the reference's labels and keys, the request and the plan.

On this build's tree before its commit, `python -m pytest tests/lab` printed `697 passed, 22738 subtests passed`, and
`python -m pytest tests/onboard tests/mycelic/test_collective_guards.py tests/mycelic/test_collective_x3.py` printed
`402 passed, 1080 subtests passed`. On the tree of the fixes after review before its commit, the first printed
`708 passed, 22854 subtests passed` and the second `402 passed, 1080 subtests passed`.

## The dry run

The lab's dry run of the template itself, on the tree of the fixes after review before its commit (`wf/l001-fix`), on
the synthetic file (no MSHA value; the file is the one `python -m tests.lab.l1_data --out DIR` writes with its default
seed) and the lab's fake model server:

```
python -m lab.dryrun --request lab/templates/latency.json --out DIR --l1-raw DIR_WITH_THE_FILE
```

It exited 0. What it printed, in order:

- `plan: 15 units in 15 shards (real)`: the template's three models times five questions, one unit per shard.
- `prereg: e1 no x1 no e2 no j1 no l1 yes`: the plan job's L1 step ran (the file, the data, the five questions, the
  four comparators on their own stores, the draws and the warm-up payloads) and wrote the manifest's `l1` block, which
  every unit then verified before its first call.
- `l1 guard: plan files 7 withheld 0 record_id 0 mine_id 0 refused_value 0 narrative_ngrams 0 unread 0`: the guard over
  the plan directory after the preregistration.
- For each of the 15 units, `lab: unit l1-<model>-q<k> status ok class plumbing exit 0` with its `wall_s`, between
  14.6 and 17.5 seconds, and the guard over its shard root three times, after the run step, after the seal and after
  the shard summary: `files 22`, `files 23` and `files 25`, each with every other count 0.
- `l1 guard: plan files 9 ...` with every count 0, after the plan summary.
- `lab: aggregate units 15 shards 15 class real measurements false lock unchanged`.
- `l1 guard: report files 3 ...` after the aggregate and `l1 guard: report files 5 ...` after the report summary, each
  with every count 0.

The whole dry run took `real 6m5.158s` (`time`). Every unit's class is `plumbing`, since its replies come from the
fake server, so `measurements false`, and each model's `l1` block in `report.json` has all five slots finished and
ok, no answer left out, no question contended or not finished, and a null headline, with the reason that not every
question of the model is a model measurement. The summaries' every number is traced to its file (`lab.summary` checks
it before writing), and the 49 guard lines withheld no file and counted no hit. The times above are this machine's on
the synthetic file and the fake server; they say nothing of the runner's times on MSHA's file with the models.

## After run 1: K14

Run 1 (`latency-001`, lab run 38090725021) stopped at the plan job's guard, before any model ran: `plan.json`, which
`lab.plan` writes before the MSHA file is read, held one refused value as a whole word (`CHOICE-L001.md`, "Runs").
K14 was committed alone (`161cb4f`, branch `wf/l001-k14`); this change carries it out, on `wf/l001-k14-build` from
`161cb4f`. No MSHA value was read for it: every run here read the synthetic file.

What changed:

- **The guard** (`lab/l1guard.py`). `build(raw, plan)`, `run(dirs, raw, plan)` and `main_guard(dirs, raw, plan)` take
  the plan's path. `plan_text` reads the plan first, before the file is split, and keeps its text only when it can be
  read as a lab plan: a regular file (a symlink is not), not a withheld file's stub, UTF-8, strict JSON, an object with
  `kind: lab_plan` and `schema_version` 1. `Guard` then removes from the backstop's record-id and site sets and from
  the refused values every value that `plan_values` finds in the plan's folded text, with the scan's own matching
  (`draft.ValueIndex.found_all` on `folded` text), and keeps the number removed from each set in `left_out`. `run`
  prints, once per invocation that built a guard and before its directory lines, `l1 guard: values in the plan's own
  text, left out: record_id <n> mine_id <n> refused_value <n>`, or, when the plan cannot be read, `l1 guard: the plan
  could not be read as a lab plan; no value is left out` (nothing is then left out, and the scan is K9's). Every other
  line keeps its format. The plan is still scanned like every other file, and the n-gram scan is unchanged.
- **The command** (`lab/msha.py`). `guard` passes `--plan` to the guard. A plan that an earlier guard withheld is no
  longer taken for a plan without L1 units: only an L1 plan's guard writes a stub, so the command runs the guard, with
  nothing left out (K14: a withheld plan leaves nothing out and the scan is K9's). With run 1's code, a guard step after
  the one that withheld `plan.json` read the stub as a plan without L1 units and left its directory unscanned.
- **The probe** (`tools/l1/guard_probe.py`, workflow `l1-guard-probe.yml`). Its first pass and its lines are
  unchanged. A second pass rebuilds the guard as K14 has it, with run 1's regenerated `plan.json` read by the guard's
  own `plan_text`, and prints the same counts as `after K14` lines: the counts left out (by set, and the refused values
  left out by column and length), the refused values kept, `plan.json` whole and by path class, J001's files and the
  lab's code and docs. Counts only, as before. A push of this file re-runs the probe.
- **The reference** (`docs/lab/REFERENCE.md`) quotes both lines.
- **Requests.** None is added and `lab/templates/latency.json` is unchanged: the caller adds
  `lab/requests/latency-002.json` (K14, "The next run"). `RequestTests.test_the_template_is_l001_s_settings` asserted
  that `lab/requests/latency-001.json` does not exist, which stopped holding when run 1's request was pushed; it now
  checks that latency-001 is the template unchanged and that any later latency request differs from it only in a
  purpose that names K14.

The tests that pin it (`tests/lab/test_lab_l1.py`). `PlanValuesTests` runs on a synthetic file whose second operator's
contractor id is the first word of one of the template plan's shard labels, so the plan as `lab.plan` writes it holds
one refused value as a whole word, as run 1's did (the label is the test's own choice; nothing here comes from MSHA):

- `test_a_refused_value_equal_to_a_shard_label_is_left_out`: K9's guard finds it in the plan; K14's leaves it out
  (`refused_value 1`), and the plan directory, a shard root and a report that carry the label pass, `plan.json` with
  its bytes unchanged;
- `test_a_refused_value_not_in_the_plan_is_still_a_hit`: another operator's contractor id and name still withhold
  their files;
- `test_the_plan_is_still_scanned`: a narrative's run in the plan's text withholds `plan.json`;
- `test_a_record_id_and_a_mine_id_in_the_plan_are_left_out_the_same_way`: both backstop sets, and others still hit;
- `test_the_line_counts_and_never_prints_a_value`: the exact lines, and no value (a document number, a mine id and an
  operator's name, each word of it) in the output;
- `test_an_unreadable_plan_leaves_nothing_out`: no plan named, missing, withheld, not JSON, not UTF-8, another kind,
  another schema, a directory, a symlink: the fixed line, nothing left out, the label's file withheld, no crash;
- `test_a_withheld_plan_is_still_guarded_through_the_command`: `lab.msha guard` with a withheld plan scans;
- `test_only_whole_words_of_the_plan_are_left_out`: a value only inside a word of the plan is not left out; one
  bounded by punctuation is;
- `test_the_command_passes_the_plan`: `lab.msha guard` leaves the plan's value out.

`GuardTests` pass the plan to the guard and expect the new line first; `DryRunTests.test_the_plan_and_the_units`
expects one plan line before each guard step's directory line, each with every count 0;
`DocsTests.test_the_guard_s_plan_lines_are_quoted_as_printed` checks the reference. Against the guard and command of
`161cb4f`, every `PlanValuesTests` and `GuardTests` case above and the docs test fail. Fourteen mutations of the new
code, applied one at a time to a copy of the tree, each failed at least one of them: the plan's text ignored, the
backstop sets kept, substring matching, unfolded matching, the kind or the schema not checked, a symlinked plan
followed, bad UTF-8 replaced, fixed counts in the line, the plan line printed for an unread plan, the plan line after
the directory lines, `plan.json` skipped by the scan, the command passing no plan, and the command skipping a withheld
plan.

On this tree before its commit, `python -m pytest tests/lab` printed `718 passed, 22968 subtests passed` (21 min 36 s),
and `python -m pytest tests/onboard tests/mycelic/test_collective_guards.py tests/mycelic/test_collective_x3.py`
printed `402 passed, 1080 subtests passed`. The probe, run here on a synthetic file built as `PlanValuesTests` builds
it, the template's plan and a stand-in for an earlier run's files, printed its first pass as before and its `after
K14` lines with `left out: record_id 0 mine_id 0 refused_value 1`, nothing in `plan.json`, and no hit left in the files
that carried the label.

The dry run of the template on this tree before its commit, on the synthetic file (`python -m tests.lab.l1_data --out
DIR`, default seed) and the fake server, as above (`python -m lab.dryrun --request lab/templates/latency.json --out DIR
--l1-raw DIR_WITH_THE_FILE`), exited 0 in `real 6m32.931s`. It printed what the earlier dry run printed (`plan: 15
units in 15 shards (real)`, `prereg: e1 no x1 no e2 no j1 no l1 yes`, the 15 units `status ok class plumbing exit 0`
with `wall_s` between 14.2 and 18.3, and `lab: aggregate units 15 shards 15 class real measurements false lock
unchanged`), and 98 guard lines: each of the 49 guard steps printed `l1 guard: values in the plan's own text, left out:
record_id 0 mine_id 0 refused_value 0` and then its directory's line, with the same file counts as before (`plan files
7` and `plan files 9`, each shard root `files 22`, `files 23` and `files 25`, `report files 3` and `report files 5`),
every one `withheld 0` with every count 0. The synthetic file holds no word of the template's plan, so nothing is left
out there; on MSHA's file, run 1's plan held one (K14, "What it costs").

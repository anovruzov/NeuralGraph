# A001 build note: the lab's `j2` experiment, as built

What was built so the lab can run accuracy test A001 (`CHOICE-A001.md`, with its section "Amended before any run,
2026-10-11", A1 to A12). The choice file is the rule; this note says how the build carries it out, where the rule left
a choice to the build, what deviates (nothing of the rule's substance), what the tests pin and what the dry run showed.
The rule was committed alone and amended before any code (`146431c`, the amended rule); this build starts from
`146431c` on branch `wf/a001` (`689e138`), and the review of that commit led to the fixes of `wf/a001-fix` (the
section Fixed after review; the rule is unchanged). While building, no NHTSA record was read: the sandbox reaches no
NHTSA host and holds no earlier run's artifact, so every run here read the synthetic complaint archive of
`tests/lab/j2_data.py`, whose every value is invented, and a synthetic earlier plan artifact built from it by the
pinned code. No model ran: every server here was the lab's fake, which answers task N as it answers the shipped task
(A10), so no figure in this note is a reading of the instruction or of the mapping.

## The shape

| Step | Where | What |
|---|---|---|
| Request | `lab/request.py` | an `experiments.j2` block: J1's keys plus `exclude` (`run_id`, `artifact`, `file`, `sha256_prefix`), `primary` (`model`, `arm`) and `comparisons_before` |
| Plan | `lab/plan.py` | one unit per model and part, `j2-<model>-p<k>`; `--github-output` adds `j2_exclude_run_id` and `j2_exclude_artifact` (A1), empty for every other plan |
| Download | `.github/workflows/mycelic-lab.yml` | `permissions: {actions: read, contents: read}`; one plan-job step between `plan` and `prereg`: `actions/download-artifact` of the excluded run's plan artifact into `${{ runner.temp }}/lab-earlier/<run id>`, only when the run id output is not empty, `continue-on-error: true` (A1) |
| Preregistration | `lab/prereg.py` (in process) over `lab/j2labels.py` | both freshness mechanisms, the stop rules, the fresh draw, J001's questions and comparators on it, `prereg/j2/excluded.jsonl`, the pins (A1 to A4) |
| Unit | `lab/units.py` runs `python -m lab.j2 run` | one model on one part, task N first, then the shipped task (A9); both routed to the one endpoint at `site:lab` |
| Warm-up | `lab/warmup.py` | `judge_record` and `judge_record_negation` on the judge's generated payloads; the probe is the shipped task's (A11) |
| Fake provider | `lab/responder.py` | `judge_record_negation` answered by the shipped judge's handler in both branches (A10) |
| Aggregate | `lab/aggregate.py` | a `j2` block: per model its parts, its two (model, task) sets, its four arms with the comparators on the same records and the verdict word, the lifts, and `primary_result` (A5, A6, A8) |
| Summary | `lab/summary.py` | the J2 lines of the plan summary and the three J2 sections of the report |
| Notes | `lab/notes.py` | the fixed sentences, columns and headings (no ASCII digit in any of them: the lab guard `test_notes_hold_no_digits` holds, so no figure can ride in a fixed sentence) |
| Template | `lab/templates/accuracy.json` | A001's settings, to copy to `lab/requests/accuracy-001.json` |

New modules: `lab/j2.py` (the two tasks, the mapping, the arms, the lines and their views, the verdict word, the
pins, the runner) and `lab/j2labels.py` (the eligible list, the draw, both mechanisms, the labels record and the
carried-forward file). New test data: `tests/lab/j2_data.py`. The request file is not added under `lab/requests/`:
pushing a file there to any branch but `main` starts a run, and the caller starts the run. The template's purpose
names `lab/requests/accuracy-001.json`, as the lab's other templates name their copies.

No pinned module was changed: nothing under `mycelic/collective/` at the top level, `detect/`, `edge/`, `packs/`,
`evaluate/`, `followup/`, `inference/`, `pushdown/`, `experiments/` or `onboard/`; not `connectors/openfda.py`,
`pilot/audit.py` or `pilot/power.py`; no `CHOICE-*`, `run-*.json` or `*-settings.json` of an earlier test; nothing
under `docs/collective/replay/vehicles/pack/`; and neither `lab/j1.py` nor `lab/goldlabels.py`, which the rule pins
(`git diff --name-only 146431c` lists none of them). `verify.judge_task`, `judge_payload`, `judge_schema`,
`lexical_judge` and `decide` are imported and unchanged, so the product's record-level behaviour is arm A and nothing
here ships an arm (the rule's "Decisions this does not make").

## How each rule is carried out

- **The data and the draw, items 1 to 6 as amended (A1 to A4).** `lab.prereg._j2` calls `j2labels.earlier_refs` on
  the downloaded directory and the request's `exclude` entry (mechanism one), and `j2labels.rebuild_refs` on the
  preregistration's one cached fetch of the complaint archive with the pinned prefix `bc6d092d8bca` (mechanism two).
  Mechanism one hashes the listed file's bytes (the `sha256` field of `labels.json` is never trusted), cross-checks the
  hash and size of that file, of `labels.json` and of `questions.jsonl` against the artifact's manifest
  `prereg/prereg.json`, requires `labels.json` to say `source` `nhtsa`, `n` 150 and `seed` 1 with `n` equal to the refs
  found, and requires the questions' refs to be the labels' refs. Every read is inside `try`/`except` and gives one of
  seven fixed outcomes (`verified`, `artifact absent`, `sha256 mismatch`, `manifest mismatch`, `refs mismatch`,
  `labels mismatch`, `malformed`); nothing read from the directory is printed or written anywhere but into the
  exclusion set on the runner. For a later cycle the listed file is `prereg/j2/excluded.jsonl`, one `{"record_ref"}`
  line per ref (A3). Neither mechanism verifying refuses the plan with `J2_FRESHNESS_UNVERIFIED`; both verifying
  prints the symmetric difference of their sets and refuses the plan above zero with `J2_EXCLUSION_DISAGREES`; the
  excluded set is the union of what the verifying mechanisms gave (A2). The eligible list is re-implemented from
  `goldlabels.py:79-87` (`j2labels.eligible_records`) and pinned equal to `goldlabels.nhtsa_lines` byte for byte by a
  test (A4); the draw is `random.Random("nhtsa:<seed>").sample` over the eligible list less the excluded refs, sorted by
  `record_ref`, in `goldlabels`' label-file shape, so `e1_extract.read_labels` and `lab.j1` read it unchanged. Fewer
  than `n` records left refuses the plan with `J2_TOO_FEW_RECORDS`, an overlap above zero raises before anything is
  written, and the plan job prints item 5's fixed line (`j2 draw: available ... excluded ... eligible_after_exclusion
  ... overlap ... mechanisms artifact=... rebuild=... labels_sha256 ... excluded_sha256 ... complaint_file_sha256 ...`)
  and the comparators' figures, never a ref. `prereg/j2/excluded.jsonl` holds every excluded ref and the draw's,
  sorted, refs only, and its sha256 prefix is in the fixed line and the labels record (A3).
- **Rule 1, the records.** The request's `labels` block is J1's with `source` `nhtsa` only (`J2_LABELS_PROBLEM`); the
  template says `n` 150, `seed` 2. The labels record (`kind: lab_j2_labels`) carries `available`, the complaint file's
  sha256 and the exclusion block; each record's codes are empty and `lab.j1.window_record` refuses one that holds any.
- **Rule 2, the questions.** `lab.j1.build_questions(pack, labels, seed, parts)` unchanged; the template says seed 2,
  parts 5, which the planner turns into 30 records and 60 questions a part.
- **Rule 3, the arms.** `lab.j2.ARMS`, `ARM_TASK` and `ARM_MAPPED` declare the four arms; `NEGATION_INSTRUCTIONS` is
  the blockquote of rule 3 (a test joins the blockquote's lines and compares them equal, and checks that the first
  three sentences and the last are the shipped instruction's); `negation_task()` is `TaskSpec("judge_record_negation",
  "raw", NEGATION_INSTRUCTIONS, JUDGE_MAX_TOKENS)`; `MAPPING` is `{"describes_predicate": {"unclear": "no"}}` and
  `mapped_reply` reads the reply alone. Every call is `Runtime.run(task, lab_j1.payload(...), judge_schema, ref=...)`
  with the shipped schema and payload (codes empty), and every verdict is `lab.j1.verdict`, the pinned `decide` over
  the one record, applied to the reply (arms A and C) and to the mapped reply (arms B and D). Model calls are made for
  the two tasks only: B and D are `lab.j2.arm_lines` views of A's and C's lines. The preregistration pins both tasks
  (name, data class, instructions sha256, schema sha256, max_tokens), the pass order, the mapping's text and sha256
  and the arms, and every unit checks them before its first call (exit 2 on any difference).
- **Rule 4, the path.** `Runtime` at boundary `site:lab`, data label `public`, J001's endpoint pins (HTTP deadline
  600 s, 1 retry), `lab.j2.routing_doc` routing both task names to the unit's one endpoint without escalation; the
  unit refuses a routing that routes either task elsewhere or escalates (exit 2).
- **Rule 5, the models.** The template names a-0p5b, a-1p5b and a-4b from `lab/models.json`, unchanged.
- **Rule 6, the comparators.** `lab.j1.lexical_verdicts`, `prior_verdicts` and `prior_bound` on the fresh draw in the
  plan job, printed as `j2 comparators: ...`; in the aggregate, each is scored again on each arm's scored records.
- **Rule 7, the scoring.** `lab.j1.score` unchanged on each arm's view, with the request's `bootstrap_b` and
  `bootstrap_seed` (the template: 10,000 and 2); a model failure is `unknown` and scored; a transport failure drops
  the record from both arms built on that task's set; a dropped share above `WITHHOLD_SHARE` 0.01 withholds the set
  (`J1_WITHHELD`), which the verdict word reads as `incomplete` (A6).
- **Rule 8, the paired differences.** `lab.j1.paired` unchanged: each arm minus the lexical judge, and the five lifts
  of `lab.j2.LIFTS` with their levers (`LIFT_LEVERS`), each with its reading (`lift_reading`: `helps`, `hurts`,
  `not_told_apart`).
- **Rule 9 and A7, the primary pair.** The request's `primary` block names P (the template: a-4b, `negation`), the
  preregistration pins it, the aggregate marks the pair `primary` and reads it out as `primary_result`; the headline
  arm is the primary's arm for every model (`headline_arm`), and `lab.j1.headline_for` gives J001's rule 7 headline on
  that arm against the lexical judge on the same records. The comparison count is `4 x models` this cycle, plus the
  request's `comparisons_before` (the template: 0), printed as `comparisons since the loop began` (the template: 12).
- **Rule 10, A5 and A6, the verdict word.** `lab.j2.pass_test` evaluates the four clauses (balanced accuracy at or
  above `TARGET_BA` 0.86, the paired difference's lower end above 0, the balanced accuracy's lower end above the bound's
  point value on the same records, the unknown share a number) and returns the figures; `lab.j2.verdict_word` reads the
  words in A5's order (`incomplete` when a part did not finish, the set is not a model measurement, `measurement` is
  not true or the dropped share is above 0.01 or unknown; `pass_provisional` when every clause holds; `partial` when the
  target is reached or the lexical judge is beaten; else `failure`). The aggregate computes the word for every (model,
  arm) pair from the figures alone and the summary prints the primary pair's sentence with its figures
  (`J2_PRIMARY_LINE`) and the words in the arms table. A6's reading of "complete" is a (model, task) set whose every
  part finished for that task (`complete_by_task`), with `measurement` true, read by the aggregate's `sets`.
- **Rule 11, beside the verdict.** Every arm's sensitivity, specificity, accuracy and unknown share with intervals,
  the answer-pair counts before and after M (`scores.answers` of each arm's view: the mapped arms' counts are after M),
  the verdict counts, failures by kind and per task, `by_predicate`, the comparators on the same records, and the
  per-task judge-call latency (`latency_ms_by_task` in each part's row, p50 and p95 from the ledgers) with the runner
  CPU in the report's units table. Arm A's figures beside J001 run 9's are the Runs entry's to write; the report holds
  arm A's figures.
- **Rule 12 and A9, the units.** The template plans 15 units of 300 minutes, one per shard, `job_minutes` 330 and
  `max_parallel` 15; each unit judges its 60 questions twice, task N first; a stop (budget, server down, signal) ends
  the pass it falls in and the passes after it, and `run.json` records `complete_by_task`, `stopped_task` and
  `questions_done_by_task`. A set is scored only when all its parts finished for that task (`parts_ok` of each set);
  its finished parts are a partial reading with `verdict_word` `incomplete`.
- **Rule 13 and A8, what is written.** A verdict line is J001's plus `task` and `mapped_verdict`
  (`lab.j2.LINE_KEYS`); `lab.j2.line_problem` requires the task name, calls `lab.j1.line_problem` on the projected
  line (the shipped verdict by the pinned `decide`) and recomputes the mapped verdict with `decide` over the mapped
  reply; the aggregate refuses a part whose file holds a line that disagrees, a (question, task) twice or a line of a
  third task. Reports, summaries and console lines hold counts, scores and hashes only; the labels, questions and
  `excluded.jsonl` files are in the plan artifact, never the repository. The warm-up sends both tasks on a generated
  record about no drawn record and records `repeat_identical` for the shipped task's probe (A11).
- **Rule 14, the first run.** Nothing here reads a reply other than through arms A to D; the aggregate's words come
  from the figures alone.
- **A10, the fake provider.** `lab/responder.py` answers `judge_record_negation` with the shipped judge's handler in
  the pack branch and the pack-free branch, and refuses an unknown name as before; `lab/units.py`'s `with_pack` list
  gains `j2`, so a dry-run unit gets the vehicle pack's fake. In a dry run arms C and D therefore equal A and B.
- **A12, the lab changes.** `lab/j2.py`, `lab/j2labels.py`, `lab/request.py`, `lab/plan.py`, `lab/prereg.py`,
  `lab/units.py`, `lab/warmup.py`, `lab/responder.py`, `lab/aggregate.py`, `lab/summary.py`, `lab/notes.py`,
  `lab/dryrun.py`, `lab/__init__.py`, `.github/workflows/mycelic-lab.yml`, `lab/templates/accuracy.json`,
  `tests/lab/test_lab_j2.py` and `tests/lab/j2_data.py`, the pins of `tests/lab/test_lab_workflow.py` named in A1
  (`permissions`, `github.token` in exactly two places, four `download-artifact` steps, the `earlier` step between
  `plan` and `prereg`), `tests/lab/test_lab_docs.py` (the J2 labels, the troubleshooting rows, the template),
  `tests/lab/test_lab_plan.py` (the two new outputs), `tests/lab/test_lab_sim.py` (the experiments message) and
  `tests/lab/helpers.py` (`j2` among the preregistered experiments); `docs/lab/REFERENCE.md`, `docs/lab/README.md`,
  `docs/lab/INTEGRATION.md`, `lab/requests/README.md` and this note. `tests/lab/test_lab_guards.py` is unchanged:
  `lab/j2labels.py` makes no network call, and `actions: read` is read-only.

## Choices the rule left to the build

- **The request's `exclude` keys (A1).** `run_id` (an int, 1 to 10^20), `artifact` (`lab-plan-<run_id>-<attempt>`,
  whose run id must be the entry's), `file` (one of `prereg/j1/labels.jsonl`, `prereg/j2/excluded.jsonl`) and
  `sha256_prefix` (12 to 64 lower-case hex digits). The plan echoes them into every j2 unit's params, the plan step's
  two outputs and the labels record's `excluded_runs` (run id, artifact, file, prefix, refs found, outcome, whether a
  stand-in was used, and the entry as requested).
- **Where the earlier artifact goes.** `${{ runner.temp }}/lab-earlier/<run id>`, never under `lab-plan`, so it is
  not uploaded, not scanned by `lab.msha guard` and not read by `lab.summary`; `lab.prereg` reads it from
  `lab-earlier` beside the plan's directory by default (`--earlier-plans` overrides, for the tests). The workflow's
  prereg command is unchanged, so the workflow test's pin on it holds.
- **The dry run's stand-in.** The sandbox and a dry run reach no NHTSA host and hold no earlier artifact, so
  `lab.prereg` takes tests-only flags (`--nhtsa-archive`, `--earlier-plans`, `--exclude-stand-in`) that `lab.dryrun
  --j2-data DIR` passes from a `tests.lab.j2_data` directory: a synthetic archive, a synthetic earlier artifact built
  from it by the pinned code, and a stand-in entry (the synthetic run id, artifact, file and prefix, plus a
  `rebuild_sha256_prefix` so that mechanism two verifies on the synthetic archive). The stand-in replaces the request's
  entry for the checks only; the preregistration records `stand_in: true`, the labels record keeps the entry as
  requested beside the one used, and the summaries print `J2_STAND_IN`. The workflow passes none of these flags.
- **The unit's status (A9).** Exit 1 with `complete_by_task` saying task N finished and the shipped task did not is
  `ok` with `J2_SHIPPED_STOPPED`; any other stop is `failed` with `J1_STOPPED`. The participation floor judges task N
  always (`required_tasks` is `[judge_record_negation]`) and the shipped task only when its pass finished, so a stopped
  shipped pass with few calls does not make the unit `invalid`.
- **Which file a bad line spoils.** The aggregate refuses a whole verdict file whose any line fails the check, for
  both tasks: the file is one. A stopped shipped pass is not a bad line: the file holds task N's lines and whatever
  the shipped pass wrote, each line checked, and the part is finished for task N alone.
- **A set's display class and measurement** are read per task over the parts finished for that task, as J1 reads a
  model's over its parts; the arms of one task share them.
- **The lifts** are paired over the records both arms score; a lift of an incomplete set is still computed over its
  finished parts and marked `complete: false`, so it decides nothing.
- **The summaries' columns.** The arms table's verdict word, pass test and primary pair columns, and the lifts
  table's lever and reading, are new columns of `lab.notes.COLUMNS`; `lift` is shared with the simulation's lifts.
- **Budget.** A J2 unit's `--budget-seconds` is its timeout less `SIM_BUDGET_MARGIN_S`, as J1's; the budget's clock
  runs over both passes.

## Deviations

None of the rule's substance. One pin of the lab's own tests was adjusted as A1 declared: `tests/lab/test_lab_workflow.py`
expected the `prereg` step to follow `plan` directly, and now expects the `earlier` step between them, with its exact
`with` block, its `if` on the run id output and `continue-on-error`. Two changes to the lab's shared code came with the
build, each tested and neither changing an earlier experiment's result: `lab.plan`'s outputs gain the two J2 outputs
(empty strings for every other plan, so the plan test's exact output lists gain two empty lines), and `lab.prereg`'s
line adds ` j2 yes` only for a plan with J2 units. `tests/lab/test_lab_sim.py`'s message for an empty `experiments`
block gains `, j2` at its end (`j2` is appended to `request.EXPERIMENTS`, so every other message is unchanged).

## Fixed after review

The review of the build commit `689e138` found two blocking and five minor problems. `wf/a001-fix` fixes each with a
test that fails without the fix, and changes nothing of the rule (a fix that would have changed it is recorded under
Known limits instead):

- **`lab.prereg.main` overrode a caller's keywords (blocking).** `main` passed `nhtsa_fetch=None`, `earlier_plans=None`
  and `exclude_stand_in=None` to `preregister` whenever a flag was absent. J001's dry-run fixture
  (`tests/lab/test_lab_j1.py`, `_J001`) stubs the NHTSA download with `functools.partial(preregister, nhtsa_fetch=...)`,
  and a call-time keyword wins over a partial's, so the fixture downloaded NHTSA's file for real and failed in the
  sandbox: `tests/lab` was red on `689e138` (four errors in `J001DryRunTests`, eight failures in J001's
  `AggregateTests`, which share the fixture). `main` now builds an options dict and passes only the options its flags
  set. `PreregTests.test_main_passes_only_the_options_given` records what `main` passes for each flag and that a
  partial's `nhtsa_fetch` survives.
- **A note with digits (blocking).** `J2_FRESHNESS_UNVERIFIED` ended "(CHOICE-A001, The data and the draw, as
  amended)": the lab guard that no note holds an ASCII digit (`tests/lab/test_lab_guards.py`,
  `test_notes_hold_no_digits`) failed, and this note's claim that the notes hold no digit was false. The note now ends
  "(the accuracy test's rule, The data and the draw, as amended)", in `lab/notes.py` and in the two copies
  `tests/lab/test_lab_docs.py` pins (`docs/lab/README.md`'s troubleshooting row and `docs/lab/REFERENCE.md`'s label
  line).
- **The pins test tested the last variant only (minor).** `prereg_with` wrote every variant to one path and the routing
  helper to one path per model, and the cases were built before any ran, so every prereg case exercised the `kind`
  variant and every routing case a same-endpoint escalation that `load_routing` itself refuses: the mapping pin and the
  escalation check could be deleted unnoticed (the review's mutations M06 and M07). Each case now writes its own file
  (the test asserts that no two share one), asserts the message of the pin that refused it, and the two escalation
  cases route through a second endpoint of the preregistration (`fake-a` escalating to `fake-b`), a routing that
  `load_routing` accepts, so only J2's check refuses it; the unchanged files then run, so the refusals were the pins'.
- **Mechanism two alone was untested (minor).** The excluded set is the union of what the verifying mechanisms gave,
  and nothing pinned the union: replacing it with mechanism one's refs (empty when the artifact is absent) passed every
  test, which is the case A2 was written for. `PreregTests.test_mechanism_two_alone_excludes_the_rebuilt_draw` runs the
  plan with an empty earlier-plans directory and the rebuild's prefix: `artifact absent` and `verified`, 150 excluded,
  overlap 0, `excluded_runs[0].refs` 0, the draw disjoint from the rebuilt records and equal to the draw mechanism one
  gives on the synthetic artifact.
- **The bound on the arm's scored records was untested (minor).** The pass test's third clause reads the bound over
  the records the arm scored (A6), but a bound over every record passed every test.
  `AggregateTests.test_a_stop_in_the_second_pass_and_a_dropped_record` now drops a record whose removal moves the bound
  and asserts the arm's `prior_bound` equals `lab.j1.prior_bound` over the 39 records it scored, that the pass test's
  `bound` is that value, and that the untouched arm's and the block's bound are the every-record one.
- **A part finished by its lines but not by its `run.json` (A6).** The review's mutation list showed one more gap: the
  aggregate's reading of `complete_by_task` was unpinned. `AggregateTests.test_what_is_not_a_finished_part` now pins
  that a part whose `run.json` says a task did not finish is not finished for it, however many lines it holds.
- **Cycle 2's symmetric-difference stop (minor, forward-looking).** A fix would change A2, so nothing changes here;
  the limit is recorded below.
- **This note** records the suite figures on the fixed commit (Tests), corrects its pins sentence and its mutation
  table (five rows the review found missed are now caught), and adds the cycle-2 limit.

## Known limits of the build

- **Cycle 2 and the symmetric-difference stop.** In the next cycle the request's `exclude` file is
  `prereg/j2/excluded.jsonl` (this cycle's chain: J001's 150 refs and this draw's 150) while mechanism two still rebuilds
  J001's seed-1 draw (150 refs). When both verify, the good case (the file unchanged, the artifact present),
  `lab.prereg._j2` computes the symmetric difference as A2 says, finds 150 and stops with `J2_EXCLUSION_DISAGREES`,
  although A3 says mechanism two checks only "the chain's oldest link". The build follows A2 literally, which is right
  for this cycle, where both sets are J001's 150. Cycle 2's CHOICE must amend A2 before its run (when the excluded file
  is `prereg/j2/excluded.jsonl`, the check is that the rebuilt refs are a subset of the chain's, printing the count of
  rebuilt refs missing from the chain and stopping above 0), and the build then branches on the file in `_j2`.

- The download step's token is the workflow's `github.token` under `actions: read`; an artifact of another
  repository, or one past its retention, is an absent directory and `J2_FRESHNESS_UNVERIFIED` unless mechanism two
  verifies. The first cycle's artifact expires on 2026-11-08 (the rule's item 1).
- Mechanism two downloads the complaint archive once per preregistration (the lab's existing `nhtsa` fetch); the
  plan job's 20-minute `prereg` step must hold the download, the rebuild and the draw. On the synthetic archive the
  whole preregistration takes well under a second; the real file's time is J001's download time plus two label builds.
- `lab.j2labels.nhtsa_rows` re-implements `goldlabels.build_labels`' row export (`goldlabels.py:132-140`) as well as
  `nhtsa_lines`' eligible build, because the pinned function exports and samples in one call; the equality test pins
  the lines, not the export counts beyond `eligible`.
- A reply that fails the schema after its repair is `unknown` under both readings, so the mapping cannot move it;
  the answer counts show it as `failed`.
- `actionlint` is not installed here; the workflow is checked by `tests/lab/test_lab_workflow.py` only.

## Tests

`tests/lab/test_lab_j2.py`, offline: the synthetic archive and earlier artifact (`tests/lab/j2_data.py`), the lab's
fake server with the vehicle pack's responder, no network host. Three fixtures are made once: a plan of two fake
models (40 records in 2 parts) preregistered against the synthetic artifact, a small dry run of two gguf models on the
fake provider with `--j2-data`, and the template's dry run with `--j2-data`.

- Rule 3: `NEGATION_INSTRUCTIONS` equal to the blockquote of `CHOICE-A001.md` (its lines joined), its first three
  sentences and its last the shipped instruction's, no pack code or predicate named in it; the task's name, data class
  and `max_tokens`; the mapping's text, its pin, its cases and that it reads the reply alone; the arms, the lifts, the
  levers, the target, the words' order; every verdict the pinned `decide`'s, on the reply and on the mapped reply.
- A8: the j1-shaped views of the four arms pass through `lab.j1.score` and `lab.j1.paired`; the line check's every
  outcome (keys, task, question, answers, verdict, mapped verdict), a consistent change kept, the shipped check
  delegated to `lab.j1.line_problem`.
- Rule 10, A5, A6, rule 9: the four clauses at their boundaries (0.860 reached at equality, the paired low end above
  0 not at it, the bound beaten strictly, the unknown share a number), the words in one order with every cell, one
  record of 150 kept and two withheld, exactly 0.01 kept, the lift readings at their boundaries.
- The data and the draw, A1 to A4: the eligible list equal to `goldlabels.nhtsa_lines` byte for byte for four (n,
  seed) pairs and `build_labels` without exclusion equal to `goldlabels.build_labels`; the draw as `random.Random`
  over the unexcluded list, no excluded ref, too few records refused with the fixed message; mechanism one verified on
  a consistent artifact (150 refs) and each fixed outcome on twenty altered artifacts (an absent directory, another
  run id, a missing or unlisted file, another prefix, a changed byte with and without the request expecting it, a
  manifest that disagrees on each file or lacks a size, other refs or fewer records in the questions, a record of
  another seed, n or source, a manifest or labels file that is not what it should be), a later cycle's
  `excluded.jsonl` with every malformed line refused; mechanism two verified on its own prefix, a mismatch with the
  pinned one and the sha returned either way, a tiny archive `malformed`, the fetch called once; the labels record's
  exclusion block, the file the pinned readers read, no ref in the record.
- The request: the defaults, thirty-two block errors (every `exclude` and `primary` clause among them), the template
  (15 units in 15 shards, `job_minutes` 330, `max_parallel` 15, the excluded run, P, the seeds), the plan's two
  outputs, `j2_exclude` for a plan with and without J2 units.
- The preregistration: its eleven files and the prereg's every key and pin, the draw with no excluded ref, the
  chain file (190 refs), the labels record's `excluded_runs`, the manifest's block, the fixed console lines and no ref
  or operator name in anything printed or written beside the labels and questions files, the plan summary's J2 lines
  with every number traced to its file; neither mechanism verifying, both verifying and disagreeing (symmetric
  difference 300), mechanism two alone (the artifact absent, the rebuild verified: 150 excluded, the draw disjoint from
  the rebuilt records), too few records, the stand-in recorded and agreeing, a stand-in that cannot be read; `main`
  passing `preregister` only the options its flags set, so a partial's `nhtsa_fetch` survives.
- The runner through the fake server: two passes, task N first, the same payload, schema and `max_tokens`, N's
  instructions in the system text, both verdicts, the ledger's refs and boundary, `run.json`'s fields, the unit
  status and participation; a reply that differs by task (C and D read N's, A and B the shipped one; `unclear` is
  `unknown` under the shipped rule and `refute` under the mapping, the answer counts before and after M); a model
  failure and a transport failure in each pass, with one repair; a stop in the second pass (N complete, the shipped
  pass not, `ok` with `J2_SHIPPED_STOPPED`, the required task N alone) and in the first (`J1_STOPPED`); the server
  staying down; twenty-two pins each refused before any call with the message of the pin that refused it, every case
  in its own file (both tasks' pins, the order, the mapping, the arms, both routes, escalation through a second endpoint
  that `load_routing` accepts, a J1 prereg), and the unchanged files then running; the unit adapter (argv, both routes,
  files, notes, participation of both tasks);
  the status reading's every pairing; the participation floor judging the shipped pass only when it finished.
- The wiring: the fake provider answers both names alike in both branches and refuses an unknown one (A10); the
  warm-up lists both tasks, the shipped one first, with the same generated payloads (A11); the plan outputs name the
  excluded run.
- The small dry run: every unit ok and plumbing, the report block (every arm equal to the lexical judge, every word
  `incomplete`, every lift 0 and not told apart, the comparisons 8, the stand-in marked), the summaries with every
  number traced and no ref anywhere.
- The aggregate, on copies of the small dry run: a-4b measured and answering task N right on every question gives
  `pass_provisional`, `better` and the instruction's lifts `helps` while the mapping's are not told apart, beside a
  measured model answering as the lexical judge (`failure`, `not_told_apart`), with the summary's primary sentence and
  table cells; twelve positives the lexical judge missed confirmed gives `partial`, and ten `unclear` negatives give
  the mapping's lift `helps` and the instruction's `hurts` with the answer counts before and after M; a tampered
  mapped verdict, a third task, a run of another preregistration and a duplicated line each leave the part unfinished
  for both tasks, a part whose `run.json` says a task did not finish is not finished for it, and a model with one part
  left is a partial reading; a second-pass stop keeps task N's set complete and the control's incomplete, a record
  dropped in task N withholds that set alone and its bound is the one over the 39 records it scored; no
  preregistration.
- The template's dry run: 15 units in 15 shards, both mechanisms verified on the stand-in, every unit ok and
  plumbing, the report block (150 records, 300 questions, 5 parts, comparisons 12, P, every word `incomplete`), the
  summaries with every number traced and no ref of the 300 anywhere.

On this build's tree before its commit, `python -m pytest tests/lab/test_lab_j2.py -q` passed its 54 tests (41 in 7 s
without the dry runs; the small dry run and the aggregate's cases in 9 s; the template's dry run in 82 s), and
`tests/lab/test_lab_docs.py`, `test_lab_workflow.py`, `test_lab_plan.py` and `test_lab_shipped.py` passed together
(the workflow's self-test included). The whole `tests/lab` suite was red on `689e138` (the review's run: 9 failed, 767
passed, 4 errors, the J001 fixture's download and the digit guard, both fixed above). On the fix commit, before it:
`PreregTests`, the pins test and `AggregateTests` passed together (16 tests, 30 subtests, 11 s); the guard
`test_notes_hold_no_digits` passed; the eleven mutations of the Mutation check were each caught; the whole `tests/lab`
suite was started in the background and had reached its J1 tests with no failure when the session had to answer, so
its final figures, the onboard and guard suites and the template dry run on this commit are the caller's to record
before merging (the session's hand-off says so).

## Mutation check

Each mutation below was applied to the working tree alone, the test file run against it, and the tree restored; the
named tests failed under each (the whole file passed on the unmutated tree before and after).

| # | mutation (what the code would then do) | caught by |
|---|---|---|
| 1 | the mapping reads `unclear` as `yes` | `RuleTests` (the mapping's text and cases) |
| 2 | the pass order swapped: the shipped task first | `RuleTests`, `RunTests` (two passes, task N first) |
| 3 | the target 0.85 | `RuleTests`, `VerdictWordTests` |
| 4 | `partial` read before `pass_provisional` | `VerdictWordTests`, `AggregateTests` |
| 5 | the target reached only above 0.860, not at it | `VerdictWordTests` |
| 6 | the lexical judge beaten at a paired low end of zero | `VerdictWordTests` |
| 7 | the bound beaten at equality | `VerdictWordTests` |
| 8 | a dropped share of exactly 0.01 withheld | `VerdictWordTests` |
| 9 | a lift `helps` at a low end of zero | `VerdictWordTests` |
| 10 | the line check skips the mapped verdict | `LineTests`, `AggregateTests` (a tampered mapped verdict) |
| 11 | the mapped arms' views keep the unmapped verdict | `LineTests`, `RunTests`, `AggregateTests` |
| 12 | the draw ignores the excluded refs | `ExclusionTests`, `PreregTests` |
| 13 | mechanism one skips the manifest cross-check | `ExclusionTests` |
| 14 | mechanism one skips the sha256 prefix check | `ExclusionTests` |
| 15 | mechanism two verifies whatever the rebuilt sha256 | `ExclusionTests`, `PreregTests` |
| 16 | a stopped shipped pass after a finished task N fails the unit | `RunTests` |
| 17 | the participation floor judges an unfinished shipped pass | `RunTests` |
| 18 | both tasks required of a unit | `RunTests` |
| 19 | the plan proceeds when neither mechanism verifies | `PreregTests` |
| 20 | two verifying mechanisms that disagree do not stop the plan | `PreregTests` |
| 21 | the negation task sends the shipped instructions | `RuleTests`, `RunTests` |
| 22 | the fake provider does not answer task N | `WiringTests`, `RunTests` |
| 23 | the aggregate reads a set complete with a part missing | `AggregateTests` |
| 24 | the aggregate accepts a (question, task) twice | `AggregateTests` |
| 25 | a judge route's escalation to another endpoint accepted (the review's M06) | `RunTests` (the pins) |
| 26 | the mapping pin not checked before the first call (M07); likewise the arms and the judge tasks | `RunTests` (the pins) |
| 27 | the excluded set from mechanism one only, so a rebuild-only verification excludes nothing (M13) | `PreregTests` |
| 28 | a part pooled for a task its `run.json` says did not finish (M16) | `AggregateTests` |
| 29 | the bound over every record instead of the arm's scored records (M17) | `AggregateTests` |

Rows 1 to 24 were checked at the build (`scratchpad/a001/mutate.py` of that session, not committed: twenty-four
caught). The review ran its own list and found rows 25 to 29 missed by the tests of `689e138` (rows 2 and 7 passed by
the pins test only because the `kind` variant stood in for every prereg case). After the fixes, `scratchpad/a001-fix
/mutate.py` (not committed) applied eleven mutations to the working tree one at a time, restoring the file's bytes
after each: rows 25 to 29, the two blocking fixes reverted, the arms and judge-tasks pins deleted, and rows 2 and 7
again; eleven caught, none missed.

## The dry run

The lab's dry run of the template itself, on this build's tree before its commit, on the synthetic archive and the
synthetic earlier artifact (`python -m tests.lab.j2_data --out DIR`; no NHTSA value, no real artifact) and the lab's
fake model server:

```
python -m lab.dryrun --request lab/templates/accuracy.json --out OUT --j2-data DIR
```

It exited 0 in `real 1m19.873s` (`time`). What it printed, in order:

- `plan: 15 units in 15 shards (real)`: the template's three models times five parts, one unit per shard.
- `j2 exclusion: both mechanisms verified; symmetric difference 0`: on the stand-in both mechanisms verify (the
  synthetic artifact's labels are the rebuilt draw of the same synthetic archive) and agree.
- `j2 draw: available 360 excluded 150 eligible_after_exclusion 210 overlap 0 mechanisms artifact=verified
  rebuild=verified labels_sha256 0044f49898fe excluded_sha256 39692965e373 complaint_file_sha256 <the archive's>`:
  item 5's fixed line; the archive's sha256 changes with each build of the synthetic zip (its timestamps), the labels'
  and the chain file's do not.
- `j2 comparators: lexical balanced_accuracy 0.717 [0.677, 0.757] record_blind 0.537 [0.487, 0.587]
  predicate_only_bound 0.547`: J001's comparators on the synthetic draw; they describe the synthetic data only.
- `prereg: e1 no x1 no e2 no j1 no j2 yes`.
- For each of the 15 units, `lab: unit j2-<model>-p<k> status ok class plumbing exit 0 wall_s 0.4`: 120 calls a
  unit against the fake server, both passes complete.
- `lab: aggregate units 15 shards 15 class real measurements false lock unchanged`.

Every unit's class is `plumbing`, so every (model, arm) pair's word is `incomplete` and the primary line reads
"Primary pair `a-4b` `negation`: `incomplete`. It scored balanced accuracy 0.717 [0.677, 0.757]; gap to the target
0.860 is 0.143; ...": the fake answers as the lexical judge in both passes, so every arm equals the lexical judge and
every lift is 0.000, not told apart. The report says `comparisons since the loop began: 12 (12 this cycle, 0 before)`,
marks the stand-in (`J2_STAND_IN`), and no record ref of the draw or of the excluded set appears in `report.json`,
`report.md`, the plan summary or the console. These figures are this machine's on synthetic data and the fake
server; they say nothing of the runner's times on NHTSA's file with the models.

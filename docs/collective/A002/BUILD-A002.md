# A002 build note: the lab's `j3` experiment, as built

What was built so the lab can run accuracy test A002 (`CHOICE-A002.md`, with its section "Amended before any build,
2026-10-13", B1 to B25). The choice file is the rule; this note says how the build carries it out, where the rule left
a choice to the build, what deviates (nothing of the rule's substance), what the tests pin and what the dry run showed.
The rule was committed alone and amended before any code (`720b0d6`, the amended rule merged); this build starts from
`720b0d6` on branch `wf/a002` (the first builder's WIP commits, stopped by the account's session limit after the docs
were written) and ends on `wf/a002-b`, which verified every WIP item against the code, added two test pins, ran the
suites, the dry run and the pinned-file check; `wf/a002-fix` then applies the review's findings on the build commit
(`6be0b0b`; "Fixes after review"). It copies A001's `j2` precedent (`BUILD-A001.md`) and changes no pinned file:
`lab/j1.py`, `lab/goldlabels.py`, `lab/j2.py` and `lab/j2labels.py` are imported unchanged (`git diff --stat
720b0d6..HEAD` over the
five pinned lab files, `lab/shard.py` included, prints nothing), `lab.aggregate.j2_block` and `lab.summary._j2_tables`
are not edited, and nothing under `mycelic/collective/`, `mycelic/*.py`, `connectors/openfda.py`, the vehicle pack,
any earlier `CHOICE-*.md`, `run-*.json`, `*-settings.json` or `research/` changed. While building, no NHTSA record was
read: the sandbox reaches no NHTSA host and holds no earlier run's artifact, so every run here read the synthetic
complaint archive of `tests/lab/j3_data.py` (`tests/lab/j2_data.py`'s archive at 90 complaints per make, B21; every
value invented) and two synthetic earlier plan artifacts built from it by the pinned code. No model ran: every server
here was the lab's fake, which answers tasks E and R as it answers the shipped judge behind an empty field, so the
grounded arms refute whatever it confirmed and no figure in this note is a reading of the instructions, the check or
the composite.

## The shape

| Step | Where | What |
|---|---|---|
| Request | `lab/request.py` | an `experiments.j3` block: `j2`'s keys with `exclude` a list of exactly two entries in seed order, each `j2`'s four keys plus `rebuild_seed` and `rebuild_sha256_prefix`, the second alone with `chain_file` and `chain_sha256_prefix` (B2, B3, B5), each entry's `file` pinned (`prereg/j1/labels.jsonl` on the first, `prereg/j2/labels.jsonl` on the second; "Fixes after review"); `primary` over `lab.j3.ARMS` (`_primary` takes the arms tuple); `comparisons_before` |
| Plan | `lab/plan.py` | one unit per model and part, `j3-<model>-p<k>`; `exclude_entries(plan)` beside `j2_exclude` (which stays); `--github-output` fills `j2_exclude_run_id` and `j2_exclude_artifact` from the first entry and `j3_exclude_run_id_2` and `j3_exclude_artifact_2` from the second, empty for every other plan (B2) |
| Download | `.github/workflows/mycelic-lab.yml` | a second plan-job step `earlier-2` after `earlier`, before `prereg`: `actions/download-artifact` of the second excluded run's plan artifact into `${{ runner.temp }}/lab-earlier/<its run id>` with `github-token: ${{ github.token }}`, `if` on `j3_exclude_run_id_2`, `continue-on-error` (B2) |
| Preregistration | `lab/prereg.py` (in process) over `lab/j3labels.py` | the five sources and the stop rules (B5), the fresh draw by the pinned `build_labels` less the union, J001's questions and comparators on it, the two ablations, `prereg/j3/excluded.jsonl`, the pins; the labels record extended with every source (B22) |
| Unit | `lab/units.py` runs `python -m lab.j3 run` | one model on one part, task E first, then R, then N (rule 14); the three routed to the one endpoint at `site:lab`; exit 1 with task E finished is `ok` with `J3_EVIDENCE_STOPPED` |
| Warm-up | `lab/warmup.py` | the three tasks on the judge's generated payloads with their own schemas and caps; the shipped judge and the extraction task are not registered for a j3 unit, so the probe is task E's (B4) |
| Fake provider | `lab/responder.py` | `judge_record_evidence` and `judge_record_rationale` answered by the shipped judge's handler behind an empty `citation` or `analysis` (`with_field`), in the pack branch and the pack-free one |
| Aggregate | `lab/aggregate.py` | a `j3` block: per model its parts, its three (model, task) sets, its nine arms with the comparators and the ablations on the same records and the verdict word, the lifts and the ablation differences, the evidence readings, the first-key shares, the token figures, and `primary_result` with the headline secondary's word (B1, B8, B19, B23); the manifest's primary pair, headline arm and secondary and the four comparator files cross-checked against `prereg/j3/prereg.json`, the document every unit pinned ("Fixes after review") |
| Summary | `lab/summary.py` | the J3 lines of the plan summary (exclusion with sources, cycles and consistency counts; the ablations) and the five J3 sections of the report |
| Notes | `lab/notes.py` | the fixed sentences, columns and headings (no ASCII digit in any sentence: `test_notes_hold_no_digits` holds) |
| Dry run | `lab/dryrun.py` | `--j3-data DIR`, refused together with `--j2-data` |
| Template | `lab/templates/accuracy-002.json` | A002's settings, to copy to `lab/requests/accuracy-002.json` |

New modules: `lab/j3.py` (tasks E and R with their schemas and instructions; g, G and the composite; the nine arms, the
lifts and the ablation names; the verdict line with its readings and `first_key`; the line check that recomputes every
reading and `grounded`; the j1-shaped views; the ablation lines; the verdict word with the ablation clauses; the
pins; the preregistration document; the runner) and `lab/j3labels.py` (`EXCLUDE_FILES`; `earlier_labels_refs`, the
A001 artifact check of item 3; the five sources with J1 and K through `lab.j2labels.earlier_refs`; the A001 rebuild
over the eligible list less J001's set; the stop rules with the by-chain readings and the consistency counts; the
union; the `excluded_runs` entries; the chain file). New test data: `tests/lab/j3_data.py`. The request file is not
added under `lab/requests/`: pushing a file there starts a run, and the caller starts the run.

## How each rule is carried out

- **The data and the draw, items 1 to 8 as amended (B2, B3, B5, B22).** `lab.prereg._j3` calls
  `lab.j3labels.establish` with the two `exclude` entries (a stand-in's in a dry run) and the preregistration's one
  cached fetch of the complaint archive. J1 is `lab.j2labels.earlier_refs` on the first entry, unchanged; R1 is
  `lab.j2labels.rebuild_refs` with the first entry's `rebuild_sha256_prefix`; A1 is `earlier_labels_refs` on the
  second entry: the same check generalised by a layout table (file, record file, questions file, the record's
  expected values, its kind), so that `prereg/j2/labels.jsonl` must hash to the entry's prefix, `prereg/j2/labels.json`
  must be a `lab_j2_labels` record of nhtsa, 150, seed 2 with `n` the refs found, the questions' refs must be the
  labels', and every file is cross-checked against the artifact's manifest; on the j1 layout it gives
  `earlier_refs`' outcome case for case on A001's own file list (a test replays A001's table through both), and for
  the chain file it calls `earlier_refs` unchanged. R2 is `lab.j2labels.draw(eligible, J001_set, 150, 2)` over
  `eligible_records` of today's file, verified when the bytes of `label_lines` over the sample hash to the second
  entry's rebuild prefix; it is attempted only once J001's set is established (`not attempted` otherwise). K is
  `earlier_refs` with the second entry's chain keys. J001's set is J1's or R1's (their union when both verified),
  else K less A1; A001's set is A1's or R2's, else K less J001's set when that came from J001's own sources; K alone
  establishes neither. The four consistency counts (`j1_r1`, `a1_r2`, `outside_chain`, `chain_union`) are computed
  where both sides exist and stop the plan above 0 with `J3_EXCLUSION_DISAGREES`; a cycle not established stops it
  with `J3_FRESHNESS_UNVERIFIED`; the excluded set is the union of every verified source's refs (300 expected, printed
  as `excluded`). The draw is `lab.j2labels.build_labels` unchanged (kind `lab_j2_labels`, `mechanisms` J001's pair),
  refusing fewer eligible unexcluded records than `n` with `J3_TOO_FEW_RECORDS`; `extend_exclusion` adds the sources,
  the cycles and the consistency counts to the record's `exclusion` block, and `excluded_runs` builds one entry per
  cycle with the artifact's, the rebuild's and (A001) the chain's outcomes and counts, `derived` `by_chain` where the
  chain reading was used, and the entry as requested. `prereg/j3/excluded.jsonl` is `chain_bytes` over the union and
  the draw (450 lines expected). The fixed console lines are `j3 exclusion: source <S> <outcome>` for every source
  that did not verify (the rebuilds with their rebuilt sha and the pinned prefix), one `j3 exclusion:` line with how
  each cycle was established, the counts and the union's size, the `j3 draw:` line with every count, outcome and
  hash, and the `j3 comparators:` line with the lexical judge, the control, the bound, both ablations and the lexical
  judge's unknown count. Nothing read from an artifact leaves the runner but the refs; no ref is printed.
- **Rule 3, the tasks.** `lab.j3.EVIDENCE_INSTRUCTIONS` and `RATIONALE_INSTRUCTIONS` are built from
  `lab.j2.NEGATION_INSTRUCTIONS` by inserting the field's sentences before its last sentence (`_with_field`), and a
  test pins each equal to the rule's blockquote (lines joined) and N's text verbatim. `evidence_schema` and
  `rationale_schema` are the shipped schema plus the string property declared first in insertion order (and first in
  sorted order), `required` sorted with the new field first, no `minLength`, `maxLength` or `pattern`; 512 output
  tokens for E and R, `JUDGE_MAX_TOKENS` for N; `task_pins` holds the three pins and the pass order. The payload is
  `lab.j1.payload`, codes empty, for all three tasks.
- **Rule 4, the readings.** M is `lab.j2.mapped_reply`, unchanged. `norm` collapses whitespace runs to one space,
  strips and case-folds; `grounded` needs at least `GROUNDING_MIN_CHARS` 4 characters after `norm` and a substring
  of `norm(text)`, `text` being the payload's `record.text` (the truncated narrative), anything that is not a string
  not grounded. G (`grounded_reply`) reads a yes as no when the citation is not grounded; `grounded_mapped_reply` is
  G then M (a test pins their commutation on every answer pair). The grounding predicate, the floor and G's rule are
  pinned as text in `grounding_pin`, checked before any call.
- **Rule 5, the arms, the composite and the ablations.** `arm_lines` builds each arm's j1-shaped view: the task's
  lines with `verdict` replaced by the reading's verdict and `describes_predicate` by the reading's answer; for C and
  Cm the verdict is then `composite_verdict` with the plan job's lexical line of the question (joined by question id;
  a composite arm without the lexical lines, or a question without one, is refused). `ablation_a_lines` is the
  composite over the constant reply yes/no (`refute` under the pinned `decide`), `ablation_b_lines` over the
  record-blind control's verdicts; both are written by the plan job as `prereg/j3/ablation_a.jsonl` and
  `ablation_b.jsonl`, scored on every record and in the aggregate on each composite arm's records. The composite rule
  and both ablations are pinned as text in `composite_pin`.
- **Rules 6 and 7, the judging path and the model.** `Runtime.run` with each task's compiled schema, one repair, no
  escalation, boundary `site:lab`, data label `public`; `routing_doc` routes the three task names to the unit's one
  endpoint and `_check` refuses a routing that routes any of them elsewhere or escalates. The template names a-4b
  alone; the validator takes any gguf or fake models.
- **Rules 8 and 9, the comparators and the scoring.** J001's code on the fresh draw, unchanged; every view passes
  through `lab.j1.score` with the request's `bootstrap_b` and `bootstrap_seed`; the bound is computed on each arm's
  scored records; the composite arms share task E's set and its completeness.
- **Rule 10, the paired differences.** `lab.j1.paired` unchanged: each arm against the lexical judge, the ten
  lifts of `LIFTS` (each named by its lever in `LIFT_LEVERS`, the field carrying its schema and cap, B18), and the
  four ablation differences of `ABLATION_DIFFERENCES`; each read by `lab.j2.lift_reading`.
- **Rule 11, the primary pair and the headline (B1).** The request's `primary` is P; `headline_arm` is P's arm and
  `headline_secondary` is `evidence_grounded`, both pinned in the preregistration; the aggregate computes J001's
  headline for both and `primary_result` carries the headline secondary's word, headline and reason beside the
  primary's, which `J3_PRIMARY_LINE` prints as the primary under the other reading of the standing default. The
  comparison count is `len(ARMS)` times the models (9 for the template) plus `comparisons_before` 12: 21.
- **Rule 12, the verdict word (B8, B17).** Every arm's word is `lab.j2.verdict_word`, unchanged; for C and Cm
  `composite_verdict_word` adds `ablation_clauses` (the paired lower ends to ablation A and to ablation B above 0,
  strict), `pass_provisional` only with both, a composite that meets the four clauses but not an ablation clause
  `partial`, and a composite whose ablation difference is not a number on either side `incomplete`, whatever the
  other clauses say (as a missing bound makes any arm). The words are evaluated in the rule's order.
- **Rule 13, reported beside the verdict.** `_j3_evidence`: per kind the yes answers and how many were grounded, the
  empty citations and their share, the citation lengths after `norm` (median and 95th percentile); `_j3_first_keys`:
  the share of a task's replies whose first key is the new field, with every first key counted; `_j3_tokens`: the
  attempts, the output tokens' median and 95th percentile, the replies cut at the cap (`finish_reason` `length`) and
  the successful calls' latency, from the ledgers, per task. The summary's evidence and tasks tables print them.
- **Rule 14, the units.** `_execute` runs the three passes in `TASKS` order, E first; a stop ends the pass it
  falls in and the passes after it; `complete_by_task`, `stopped_task` and `questions_done_by_task` are written;
  `_j3_status` reads exit 1 with task E finished as `ok` with `J3_EVIDENCE_STOPPED`, a stop in the first pass as
  `failed` with `J1_STOPPED`; `required_tasks` is `[judge_record_evidence]` and the participation floor judges a
  later task only when its pass finished. The budget timer and the signals are J001's (`lab.j1._Signals`), armed at
  the start of the run over the three passes (B20).
- **Rule 15, what is written (B19, B23).** `verdict_line` holds J001's keys plus `task`, `citation`, `analysis`,
  `grounded`, `first_key` (`next(iter(reply))`, the parsed reply's document order, read at write time and never
  recomputed), `mapped_verdict`, `grounded_verdict` and `grounded_mapped_verdict`, each by the one pinned `decide`.
  `line_problem` checks the keys, the task, J001's check on the projected line, the field the task carries, the
  first key for shape (one of the task's schema property names), the mapped verdict, `grounded` recomputed from the
  stored citation and the record text, and both grounded verdicts; the aggregate rebuilds each drawn record's text
  from the plan artifact's labels through `lab.j1.payload` (the pack loaded from the repository) inside the aggregate
  job only, and a refusal names a key, never a value. The report, the summaries and the console hold counts, scores,
  lengths and hashes: no narrative, citation, analysis or ref (the dry-run tests scan every one of them for every
  drawn and excluded ref and for the drawn narratives' first forty characters).
- **Rule 16, the first run.** Nothing in the lab re-runs a unit; the dry run and the tests exercise the plan job's
  stop rules and the fixed lines a re-run's Runs entry would record.

## Choices the rule left to the build

- **The request's `exclude` list (B3).** Each entry carries `j2`'s four keys with the j3 file list
  (`prereg/j1/labels.jsonl`, `prereg/j2/labels.jsonl`, `prereg/j2/excluded.jsonl`), `rebuild_seed` and
  `rebuild_sha256_prefix`; the file is pinned per entry as the rule's "The request" names them, `prereg/j1/labels.jsonl`
  on the first and `prereg/j2/labels.jsonl` on the second, the chain file read through the second entry's chain keys
  alone ("Fixes after review"); the first entry's `rebuild_seed` must be 1 (the pinned seed-one rebuild) and the
  second's above it; the chain keys are required on the second entry and refused on the first. The plan echoes the
  list into every j3 unit's params and the four plan outputs; the labels record's `excluded_runs` holds both entries as
  requested beside the ones used.
- **Where the second artifact goes (B2).** The same `lab-earlier` directory, under its own run id, so
  `lab.prereg --earlier-plans` takes one directory for both; `lab.j3labels` reads each by `<directory>/<run id>/`.
- **The dry run's stand-in.** `tests.lab.j3_data` builds the archive at 90 per make, a J001-shaped artifact at seed 1
  (`tests.lab.j2_data.earlier_artifact`), an A001-shaped artifact at seed 2 built from the same archive less the
  J001 stand-in's refs by `lab.j2labels.build_labels` with `lab.j1.build_questions` and the chain file, and a
  two-entry `stand-in.json` whose prefixes are the synthetic files', so every one of the five sources verifies in a
  dry run; `lab.prereg.load_stand_in` accepts a list for J3 and an object for J2, and `preregister` hands each kind
  only its own shape. The test fixture `_Prereg` instead carries the real runs' rebuild prefixes, so that the
  rebuilds fail there (`sha256 mismatch`) and the by-artifact path with one source per cycle is exercised.
- **The unit's status.** Exit 1 with `complete_by_task` saying task E finished and a later task did not is `ok` with
  `J3_EVIDENCE_STOPPED`; any other stop is `failed` with `J1_STOPPED`.
- **Which file a bad line spoils.** As A001: the aggregate refuses a whole verdict file whose any line fails the
  check, for the three tasks; a stopped later pass is not a bad line.
- **A set's display class and measurement** are read per task over the parts finished for that task; the arms of
  one task share them; C and Cm share task E's.
- **The views' answer pairs.** A grounded or mapped arm's view carries the reading's `describes_predicate`, so its
  answer counts are the pairs after G or M, as rule 13 asks; the composite arms' answer pairs stay the model's
  (the grounded arm's), so ablation A's answer pairs (the constant reply) differ from the lexical judge's even where
  their verdicts are the same line for line.
- **The summaries' columns.** The arms table adds the letter, the reading, the two ablation differences and the
  headline secondary flag; the lifts table lists the ten lifts and the four ablation differences with the lever or
  the ablation in one column; two new tables print the evidence readings and the per-task figures. The new entries
  of `lab.notes.COLUMNS` are documented in `docs/lab/REFERENCE.md`.
- **The citation's length** is measured after `norm`, as the rule's "citation length in characters after `norm`"
  says, so a copy that differs in spacing has the record's length.
- **Budget.** A J3 unit's `--budget-seconds` is its timeout less `SIM_BUDGET_MARGIN_S`, as J2's; the clock runs over
  the three passes.

## Deviations

None of the rule's substance. Where the build plan's names and the lab's pins differ from what was built:

- `lab/summary.py` holds `J3_ARMS`, `J3_LIFTS`, `J3_ABLATION_DIFFERENCES` (the four difference names, which the
  lifts table iterates), `J3_TASKS`, `J3_SOURCES`, `J3_CYCLES` and `J3_CONSISTENCY`, not a `J3_ABLATIONS` of the two
  ablation names: the summary never iterates the ablation names alone, and a test pins every copy equal to the
  module's constants (B11).
- The aggregate has no `_j3_composite`: the composite arms are views built by `lab.j3.arm_lines` with the lexical
  lines, and `_j3_arm` adds the ablation comparators, the paired differences to both and the composite word.
- The A001 artifact check agrees with `lab.j2labels.earlier_refs` case for case on A001's own file list; for the one
  file A001's list lacks, `prereg/j2/labels.jsonl` asked of a j1-shaped artifact, the outcomes differ by
  construction (`artifact absent` here, `malformed` there), and the test pins that one difference.
- `first_key` is recorded for every task, task N's included (the shipped schema's keys); the first-key share is
  reported for E and R only, task N having no new field (`field` null, share null).
- Two pins of the lab's own tests beyond the ones the build plan named changed with the template:
  `tests/lab/test_lab_docs.py`'s copy-target rule now reads a template whose name ends in a three-digit cycle number
  (`accuracy-002.json`) as copied under its own name, the others gaining `-001`, and `tests/lab/test_lab_shipped.py`'s
  template list gains the new template. The pins the plan named changed as it said: `test_lab_workflow.py`
  (`github.token` in three places, the two `github-token` steps, five `download-artifact` steps with a name of which
  three are own, the order `plan`, `earlier`, `earlier-2`, `prereg`), `test_lab_plan.py` (the two output keys),
  `helpers.py` (`PREREG_EXPERIMENTS`), `test_lab_docs.py` (the note names, the request keys, the template list) and
  `test_lab_sim.py` (the experiments message).
- `lab/notes.py`'s J3 columns are `j3_letter`, `j3_secondary` and `j3_replies` beside the ones the tables need; no
  `j3_other_word` column, the headline secondary's word being printed by `J3_PRIMARY_LINE`.
- The template's `purpose` carries the identifiers A002, `CHOICE-A002.md` and `accuracy-002.json` and no other digit,
  and ends with the copy target, inside the request validator's 500-character cap.

## Fixes after review

The review of the build commit `6be0b0b` found no blocking and seven minor findings. `wf/a002-fix` applies each one
that asks for a change, with a test that fails without it (checked by mutation: each test run with its fix removed),
and changes nothing of the rule:

- **The per-entry file was not pinned (`lab/request.py`, `_j3_exclude_entry`).** Either entry accepted any of the
  three `lab.j3labels.EXCLUDE_FILES` (the chain file as the first entry's `file`, J001's labels file as the second's),
  while "The request" names `prereg/j1/labels.jsonl` for J001 and `prereg/j2/labels.jsonl` for A001; not fakeable (a
  wrong file loses a source or trips a consistency stop, never widens a draw) and the template was right. The validator
  now requires the first entry's `file` to be `prereg/j1/labels.jsonl` (`J3_FIRST_FILE_PROBLEM`) and the second's
  `prereg/j2/labels.jsonl` (`J3_SECOND_FILE_PROBLEM`), raised at `<entry>.file` after the list check, so the chain
  file is read through the second entry's chain keys alone. `RequestTests.test_every_block_error` gains the four
  cases (the first entry with `prereg/j2/labels.jsonl` and with the chain file, the second with `prereg/j1/labels.jsonl`
  and with the chain file), `test_defaults` pins the accepted pair, and the docstring block of `lab/request.py` and the
  `exclude` row of `docs/lab/REFERENCE.md` say so; without the pin the four cases fail. The finding's optional second
  check, that an entry's `rebuild_sha256_prefix` equal its `sha256_prefix`, is not applied: the rule's validation list
  does not name it, a differing rebuild prefix can only lose a source (`sha256 mismatch`), and the test fixture
  `_Prereg` carries the real runs' rebuild prefixes beside the synthetic files' labels prefixes on purpose, so that the
  rebuilds fail and the by-artifact path with one source per cycle is exercised ("Choices the rule left to the build").
- **The aggregate read the primary pair, the headline arm and secondary and the comparator lines from the manifest
  alone (`lab/aggregate.py`, `j3_block`).** `load_prereg` ties `prereg/prereg.json` to the plan's sha, while the units
  pin `prereg/j3/prereg.json` (`_j3_part` checks its sha against each run.json), and that document carries the same
  `primary`, `headline_arm`, `headline_secondary` and the four comparator files' sha256; it was loaded and never
  cross-checked (A001's `j2_block` has the same shape and is left as it is). `j3_block` now refuses the block with
  `PREREG_MISSING` unless the document's `primary`, `headline_arm` and `headline_secondary` equal the manifest's and
  each of `lexical.jsonl`, `prior.jsonl`, `ablation_a.jsonl` and `ablation_b.jsonl` hashes to the document's sha256 for
  it, so an edited manifest can name neither another primary pair nor other comparator lines than the units verified
  before judging. `AggregateTests.test_a_manifest_that_disagrees_with_the_preregistration` edits the manifest's
  `primary.arm`, `primary.model`, `headline_arm` and `headline_secondary` in turn and rewrites each comparator file
  restamped in the manifest, expecting the block refused each time and read again once the files are put back; without
  the cross-check every one of its eight cases computes a block.
- **Two of `_j3_part`'s run.json identity checks were unpinned (`endpoint` and `verdicts_sha256`).**
  `AggregateTests.test_what_is_not_a_finished_part` now pins a run.json of another endpoint (part 2 of a-1p5b refused
  with endpoint a-4b and finished again with its own) and a verdicts file replaced under a run.json whose
  `verdicts_sha256` is the old file's, the shard re-sealed (refused for every task although every line passes the check;
  finished once run.json is restamped). The review's mutation, both checks dropped, fails at the endpoint case; the hash
  check dropped alone fails at the hash case.
- **The composite's word was pinned to its own re-derivation.**
  `AggregateTests.test_the_grounding_check_and_the_composite_s_words` keeps the re-derivation and asserts the word
  literally: Eg is right on every question of the fixture, so the composite errs only where the lexical judge confirms
  a negative (at most 11 of 40 on this draw, balanced accuracy at or above the target), and its word is
  `pass_provisional` with both ablation clauses true.
- **The whole `tests/lab` on a full sandbox (environmental, no code change).** The review's run of the rest of
  `tests/lab` failed 70 tests, every one in `lab/provision.py`'s disk preflight (about 440 MiB free, 1024 MiB needed) in
  files the build does not touch; the fix stage's run is under Tests, and the merge gate must run `tests/lab`, the
  onboard and the guard suites on a runner with at least about 1.5 GiB free before merging.
- **`tests/lab/test_lab_sim.py` `test_wall_time` (pre-existing, environment-dependent, no change).** It bounds two
  sim_small runs against the fake server at 60 s of wall time and fails on this sandbox's speed, on the build commit and
  on `720b0d6` alike; if the gate trips on it, a re-run on an idle runner is the evidence, not an edited bound.
- **The build commit's message said 63 tests** where `pytest --collect-only` reported 64. The branch was never pushed,
  so the record is corrected here: `tests/lab/test_lab_j3.py` holds 65 tests after this stage (the 64 plus the manifest
  test), and the fix stage's commit message quotes 65.

## Known limits of the build

- The fake server answers tasks E and R as the lexical judge behind an empty field: in a dry run Eg and Egm refute
  every question the fake would have confirmed (balanced accuracy 0.5), the composite arms equal the lexical judge
  (lexical unknown count 0 on the synthetic draw, so ablation A is the lexical judge line for line and the
  ablation-A clause repeats the lexical clause, B8), the first-key share is 1.0 for E and R, and no dry-run figure
  reads the instructions, the check or the composite.
- `first_key` relies on `Runtime.run` returning the parsed reply in document order (`jsonparse.extract_object` over
  `jsonio.strict_load` with an `object_pairs_hook`); the runner test pins it with replies whose field comes first and
  last. A reply the server re-orders is recorded as written, which is the rule's reading.
- After both earlier artifacts expire (J001's on 2026-11-08, A001's about two days later), J1, A1 and K are
  `artifact absent` and each cycle is established by its rebuild alone; a changed complaint file then fails both
  rebuilds and stops the plan with `J3_FRESHNESS_UNVERIFIED`, as the rule's amendment says the loop asks.
- R2 needs J001's set: with J1 and R1 both failed and A1 verified, J001's set comes from K less A1 and R2 is
  rebuilt over it; with K failed too, R2 is `not attempted` and the plan stops.
- The A001 artifact's `labels.json` must say kind `lab_j2_labels`, nhtsa, 150, seed 2 with `n` the refs found: the
  shape `BUILD-A001.md` recorded; a later cycle's artifact (`prereg/j3/`) is not a layout this check reads, and the
  next rule names its files.
- The plan job's 20-minute `prereg` step must hold the complaint-file download, two rebuilds and the draw; on the
  synthetic archive the whole preregistration takes about a second.
- `actionlint` is not installed here; the workflow is checked by `tests/lab/test_lab_workflow.py` and
  `tests/lab/test_lab_j3.py` (`WiringTests`) only.
- Rule 13's reading of N and D beside A001 run 1's arms C and D (a between-draw difference) is written by hand in
  the Runs entry from A001's recorded figures: the aggregate holds no figure of another run, as A001's held none of
  J001's.
- The budget timer is pinned as A001's tests pin it: the runner's clock is the test's and `setitimer` is mocked, so
  the test reads the stop in each pass without arming a real alarm; the real timer is J001's `_Signals`, called
  unchanged and tested in J001's suite.

## Tests

`tests/lab/test_lab_j3.py`, offline (65 tests after the fix stage, every rule clause of the build plan's declared list
pinned): the
synthetic archive and both earlier artifacts (`tests/lab/j3_data.py`), the lab's fake server with the vehicle pack's
responder, no network host. Three fixtures are made once: a plan of two fake models (40 records in 2 parts)
preregistered against the synthetic artifacts with the real runs' rebuild prefixes, a small dry run of two gguf
models on the fake provider with `--j3-data`, and the template's dry run with `--j3-data`.

| Class | Pins |
|---|---|
| `RuleTests` | E's and R's instructions equal to the rule's blockquotes and N's text with the sentences inserted before the last; every `rule N` reference's item title (B10); the schemas' properties, `required`, no strict keyword, the new field first, `max_tokens` 512 and 256, the pins; g's declared cases over the truncated payload text; G and M on every answer pair and their commutation; the composite rule on every verdict pair and both ablations on every line; the arms, lifts, levers, ablation differences, the headline secondary, the keys, the summary's copies, `j3labels`' pins; the routing of the three tasks; every reading's verdict as the pinned `decide`, `first_key` as written |
| `LineTests` | the nine arms' views through `lab.j1.score` and `lab.j1.paired`; the line check's every outcome (keys, task, question, answers, each reading, `grounded` recomputed against the text, `first_key` for shape only, the fields of the other tasks) |
| `VerdictWordTests` | the words in A5's order with the ablation clauses; `incomplete` for a missing ablation figure; `partial` for an ablation clause failed; equality fails the strict clauses (B17); the non-composite word is A001's |
| `ExclusionTests` | the archive fits three draws (B21); the A001 artifact check on a consistent artifact and its every fixed outcome (a changed byte, a manifest that disagrees, a record of another seed, n, source or kind, a questions file of other refs, an absent directory, an absent file, a malformed line); its agreement with A001's check on the j1-shaped artifact case for case; the A001 rebuild; every source verifying and each cycle by artifact alone, rebuild alone and both agreeing; each cycle by chain with the other established; K alone; neither verifying; R2 `not attempted`; every consistency count stopping; the labels record's sources, cycles, counts and `excluded_runs` (B22); the draw over the eligible list less the 300 |
| `RequestTests` | the defaults with the file accepted on each entry; every block error, the j3 file list, the file pinned per entry (a file of the list on the wrong entry refused, four cases) and the two entries' rules included (B3); the template's 5 units in 5 shards and its outputs; `plan_outputs` and `exclude_entries` on a j3, a j2 and another plan (B2) |
| `PreregTests` | every preregistered file and pin; the draw disjoint from both earlier draws, the chain file (340 lines), the record and the fixed console lines without a ref; the plan summary with every number traced; the stop rules through the CLI (no source verifying, verified sources disagreeing, too few records); the stand-in recorded; a stand-in that cannot be read or is the other kind's |
| `RunTests` | three passes, task E first, each task's schema, cap and instructions, the same payload, the fake's answers behind the empty field; grounded, invented, late and short citations as written (B19); a model failure and a transport failure in each pass; a stop in the second, the third and the first pass; the server staying down; every pin refused before any call (the three tasks, the mapping, the grounding, the composite, the arms, the lifts, the three routes and escalations, the files, the code hash); the unit adapter; the status reading; participation |
| `WiringTests` | both fake handlers in both branches; the warm-up's three tasks with the probe task E (B4); the plan's four outputs; the workflow's `earlier-2` step of the `earlier` step's shape (the same pinned action, the workflow's own token, `continue-on-error`, the same directory by its own run id) between `earlier` and `prereg`, under the workflow's read-only `actions: read`, `contents: read` that no job widens (B2); every J3 note, heading and column of `lab/notes.py` free of ASCII digits, the primary line's placeholders the ones the summary fills; `lab.dryrun` refusing `--j2-data` and `--j3-data` together before any plan is written |
| `SmallDryRunTests` | every unit on the fake provider; the report block (sets, nine arms with the ablation clauses, lifts, ablation differences, evidence readings, first keys, tokens, `primary_result` with the headline secondary); the summaries with every number traced and no ref or narrative anywhere |
| `AggregateTests` | every arm `pass_provisional` with grounded quotes and the ablation clauses; the grounding check removing false confirms only, the composite's ablation-A difference equal to its lexical difference (B8) and its word pinned to the value `pass_provisional`; `partial` and the mapping's lift; what is not a finished part (`grounded` against the text, the grounded verdict, a first key outside the schema, a citation on an N line, a fourth task, another preregistration, a line twice, a run.json of another endpoint, a verdicts file under a stale `verdicts_sha256`); a stop in the second pass sparing task E and a dropped record withholding E's arms; a manifest that disagrees with `prereg/j3/prereg.json` on the primary pair, the headline arm or secondary or a comparator file's bytes; without the preregistration |
| `TemplateDryRunTests` | 5 units in 5 shards; every source verified; 240 eligible after exclusion, overlap 0; 21 comparisons; every word `incomplete`; the chain file of 450 lines; no ref anywhere |

The other lab suites run unchanged but for the pins above; `tests/lab/test_lab_j2.py` passes on the unchanged
`lab/j2.py` and `lab/j2labels.py`.

Run in the build's last stage (`wf/a002-b`): `python -m pytest tests/lab/test_lab_j3.py -q` gave `61 passed, 369
subtests passed` before the three pins of `WiringTests` were added, each of which then passed alone; `python -m pytest
tests/lab/test_lab_j2.py -q -p no:cacheprovider` gave `56 passed, 204 subtests passed`; the template dry run gave the
block above; `git diff --stat 720b0d6..HEAD` over the five pinned lab files printed nothing. The whole `tests/lab`,
the onboard and guard suites and a re-run of the whole `test_lab_j3.py` were started in the background and had not
finished when the stage's limit closed it; the merge gate runs them before any merge.

Run in the fix stage (`wf/a002-fix`, "Fixes after review"): `python -m pytest tests/lab/test_lab_j3.py
tests/lab/test_lab_j2.py -q -p no:cacheprovider` gave `121 passed, 624 subtests passed` (65 and 56 tests; every new pin
fails with its fix removed: the four file cases, the eight manifest cases, the endpoint case under the review's E1+E2
mutation and the hash case under E2 alone); the template dry run gave the block below again (the complaint-file prefix
of its own generation); `git diff --stat 720b0d6..HEAD` over the five pinned lab files printed nothing, and the only
hunks in `lab/aggregate.py` and `lab/summary.py` since `720b0d6` are insertions after `j2_block` and `_j2_tables` end.
SUITES_SENTENCE

## The dry run

`python -m tests.lab.j3_data --out DIR` then `python -m lab.dryrun --request lab/templates/accuracy-002.json --out OUT
--j3-data DIR` (the synthetic archive; every hash below is a synthetic file's):

```
plan: 5 units in 5 shards (real)
j3 exclusion: j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 chain_union=0 excluded 300
j3 draw: available 540 excluded 300 eligible_after_exclusion 240 overlap 0 sources J1=verified R1=verified A1=verified R2=verified K=verified j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 chain_union=0 labels_sha256 f95b4ed9db9c questions_sha256 f5583449deab excluded_sha256 c1e1a63cee69 excluded_lines 450 complaint_file_sha256 2c07cbd47bd3
j3 comparators: lexical balanced_accuracy 0.700 [0.660, 0.740] record_blind 0.503 [0.447, 0.560] predicate_only_bound 0.577 ablation_a 0.700 [0.660, 0.740] ablation_b 0.610 [0.553, 0.667] lexical_unknown 0
prereg: e1 no x1 no e2 no j1 no j3 yes
lab: unit j3-a-4b-p1 status ok class plumbing exit 0 wall_s 1.1
lab: unit j3-a-4b-p2 status ok class plumbing exit 0 wall_s 0.9
lab: unit j3-a-4b-p3 status ok class plumbing exit 0 wall_s 0.5
lab: unit j3-a-4b-p4 status ok class plumbing exit 0 wall_s 0.6
lab: unit j3-a-4b-p5 status ok class plumbing exit 0 wall_s 0.6
lab: aggregate units 5 shards 5 class real measurements false lock unchanged
```

The synthetic archive is a zip whose one entry carries its generation time, so `complaint_file_sha256` differs from
one generation of the test data to the next while the labels', the questions' and the chain file's hashes are fixed
by the seed (the same three prefixes on every run of the dry run and in `TemplateDryRunTests`).

Every source verified on the stand-in, eligible after exclusion 240 (at least 150, B21), overlap 0, every unit `ok`
and `plumbing`, every verdict word `incomplete` (nothing measured a model), the comparisons 21 (9 this cycle, 12
before), the primary line printed with the headline secondary's word beside it, every number of both summaries traced
to a run file, and no ref of the draw or the excluded set anywhere. As the rule says of a dry run: the fake answers E
and R as the lexical judge with an empty field, so Eg and Egm refute every question the fake would have confirmed, and
nothing here measures the instructions, the check or the composite.

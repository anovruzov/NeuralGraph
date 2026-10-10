# Lab integration with the collective layer

The lab never edits `mycelic/`. It runs the collective layer's harnesses as they are, so every difference between
the collective it was built on (commit `b861362`) and the collective's phase-2 work (branch
`mycelic-collective-phase2`) is handled on the lab side, by data shape or by a public signature or parser, never by a
version string. This page records the trial merge that checks it, the shims, the open notes, the hooks that would
remove the shims, and what phase 2 offers that the lab does not use yet.

## 1. The trial merge

The trial merge ran in a throwaway clone (`git clone --shared` of the lab worktree into `/dev/shm`): nothing was
fetched back, pushed or committed outside the clone, no branch changed, and the clone was deleted afterwards.

- Collective: branch `mycelic-collective-phase2` at `b0af5b8` ("Phase 2 audit round 3"). Its merge base with the lab
  is `b861362`; it changes files under `mycelic/`, `tests/`, `docs/` and `demo/`, none of the lab's.
- Lab: branch `mycelic-cloud-lab` at `3095c46` (G6), plus the uncommitted G7 changes laid on top.
- The merge had no conflicts. It is a merge commit, not `--no-commit`, because `e1_extract` refuses a dirty
  `mycelic/collective` and the lab never passes `--allow-dirty`; the commit lived only in the clone.

The commands, with `$M` the clone and `$L` the lab worktree:

```
git clone -q --shared --no-checkout $L $M
git -C $M checkout -q --detach 3095c46
git -C $M -c user.name=lab-trial -c user.email=lab-trial@invalid \
  merge -q --no-ff --no-edit origin/mycelic-collective-phase2
git -C $L diff HEAD --binary | git -C $M apply --index
git -C $L ls-files --others --exclude-standard
python -m pytest tests/lab -q -p no:warnings
python -m lab.dryrun --request lab/requests/plumbing-001.json --out <empty dir>
```

The untracked files the fifth command lists were copied into the clone, and the last two commands ran in it. The
results, copied from the run output (the plumbing numbers come from the fake model and measure nothing):

- `tests/lab` on the merge: `511 passed, 14269 subtests passed in 554.24s (0:09:14)`; on the lab's own base, the
  same command: `511 passed, 14263 subtests passed in 522.06s (0:08:42)`.
- The plumbing dry run on the merge: exit 0; the report's first line is `PLUMBING CHECK: no model was run` and the
  log's last line `lab: aggregate units 12 shards 5 class plumbing measurements false lock unchanged`. The
  simulation's scorecard there has the control block `{"records": 845, "replay_misses": 0, "replayed_calls": 845,
  "world": "no-plant"}` and the `chance_control` note; on the lab's base the same dry run exits 0 with `control` null
  and the `no_control` note.
- On the merge every shim takes its phase-2 path: `HARNESS_CONTROL` and `E2_CENTRAL_CONTEXT` are true and
  `EXTRACTION_ERROR_KINDS` ends with `not_sent`; on the base they are false and the kinds are
  `inference.errors.KINDS`.

The merge's `tests/lab` run came before this section was written, the base's after; the documentation tests
(`tests/lab/test_lab_docs.py`, `tests/lab/test_lab_guards.py`) ran again on both trees with the section in place.

## 2. Shims

Each shim is tested on the lab's own base (where it takes the old path) and was exercised on the trial merge (where
it takes the new one). Remove each when the lab lands on the phase-2 collective.

- C1, E1's paired entry (`lab/aggregate.py`, `_e1_paired`; `lab/summary.py`'s paired table). Phase 2's `e1_extract
  compare` decides non-inferiority on micro field F1: its paired entry holds a `field_f1` object (`diff`, `ci95`)
  and a `per_record_field_f1` object (`mean_diff`, `sign_p`) and a `withheld_reason`; the base entry holds
  `mean_diff`, `ci95` and `sign_p` at the top level. The lab reads the shape (`field_f1` is an object or not) and
  reports `decision_metric` (`micro_field_f1` or `per_record_mean_field_f1`), `diff` and its interval, and the
  per-record mean difference and sign test. Remove when the lab lands on the phase-2 collective: read the phase-2
  shape only.
- C2, E2's central context (`lab/units.py`, `E2_CENTRAL_CONTEXT`; `lab/plan.py`, `central_context_tokens`;
  `lab/manifest.py`, a hosted entry's `context_tokens`; `lab/request.py`, `HOSTED_CENTRAL_CONTEXT`). Phase 2's
  `e2_pushdown run` refuses `--central-routing` without `--central-context-tokens`, the context one central request
  gets, and withholds the bar verdict when a central_raw prompt may have reached it. The plan gives every E2 unit
  `central_context_tokens` (the model's `e2_ctx_per_slot` for `self`, the hosted entry's `context_tokens` for a
  hosted central), and the argv carries the flag when the installed harness's `run` parser has it
  (`E2_CENTRAL_CONTEXT`, read from `e2_pushdown._parser()`). The request rule that a hosted central needs
  `context_tokens` holds on both. Remove when the lab lands on the phase-2 collective: always pass the flag.
- C3, the simulation's no-plant control (`lab/sim.py`, `HARNESS_CONTROL`, `ObservedRuntime`'s replay memo,
  `_score`, the schemas; `lab/aggregate.py`'s `found_net` and `chance_found`; `lab/summary.py`'s columns). Phase 2's
  `harness.channel_block` needs the same seed's world without the plant (`control_events`) and a stale chain's
  candidate weeks, scores chance finds, and lifts on finds net of chance (`harness.net_found`). When the installed
  `channel_block` takes `control_events`, the simulation runs the control world through both pipelines, replaying
  the planted run's model extractions (no second model call; misses are sent, ledgered and counted), and scores the
  channels and lifts as X1 does. The scorecard's channel and lift schemas are the harness's own
  (`harness._CHANNEL_BLOCK`, `harness._LIFT`, private names that exist on both), so one code path tracks either. Remove
  when the lab lands on the phase-2 collective: always run the control.
- C4, G0's model path (`lab/units.py`, `g0_status`; `lab/aggregate.py`'s `model_path_problems`). Phase 2's
  `leakage.json` has a `model_path` block whose `problems` fail the scan (exit 1, `passed: false`) even when nothing
  leaked. The lab maps problems without a leak to `invalid` (`G0_MODEL_PATH`) instead of `result_fail`, which
  readers take for a leak; a leak with problems stays `result_fail`. The rule runs after the participation check,
  so a run whose model never answered keeps the participation reason that names the task. Remove when the lab lands
  on the phase-2 collective: keep the rule, drop the shape test.
- C5, extraction error kinds (`lab/sim.py`, `EXTRACTION_ERROR_KINDS`). Phase 2's extraction circuit breaker records
  records it held back as error kind `not_sent`, which is not among `inference.errors.KINDS`; the simulation's per-site
  errors add it when `edge.extract` defines `NOT_SENT`. Remove when the lab lands on the phase-2 collective, or when
  `not_sent` joins a public kinds tuple.

The tests read collective outputs by shape too: E1's paired values, the routing pins (the parsed endpoint's
attributes against the preregistration, as the harness pins them), G0's exit code (1 when `leakage.json` lists
model-path problems), the scorecard's notes and control block, and the sim channels' net counts.

## 3. Open notes

The nine notes formerly in `lab/__init__.py`, as they stand after the trial merge:

1. `e2_pushdown` lets an uncaught error end the run with exit 1; the lab maps it to `E2_ABORTED` and keeps the
   ledgers. Phase 2 catches a failed central call (the item is unscored and the bar verdict withheld), so central
   failures no longer abort; the mapping stays for any other uncaught error.
2. `lab.warmup.e2_worst_payloads` mirrors the private payload shape of `e2_pushdown`'s central raw reading; a test
   pins it against the harness's own requests and passes on the trial merge. A public payload builder would remove
   the copy.
3. E2's site ledgers live under the run's `work/seed-*/edge/`; the lab collects exactly those, unchanged on the trial
   merge.
4. `e1_extract compare --run-dirs` is the one harness option taking several values; unchanged.
5. The audit's verifier and extractor circuit breaker and the runtime's checked synthetic exemptions were merge
   risks for the lab's synthetic-labelled runs. Resolved by the trial merge: the all-experiments dry run and the
   plumbing dry run pass there.
6. E2's central task has `max_tokens` 64 (`CENTRAL_MAX_TOKENS`, unchanged in phase 2). A hosted reasoning model can
   spend it thinking; the hosted preflight fails such a key (`HOSTED_LENGTH`).
7. The client always sends `temperature` 0 and, for `json_schema`, a strict schema (unchanged in phase 2). A hosted
   model that refuses either fails the preflight; a hook to omit `temperature` would admit those models.
8. `e1_extract run` writes the host's whole `/models` listing into `run.json` (`models_listed`, unchanged in phase 2),
   which is public; `e2.json` records the hash of the scratch central routing file, not its URL.
9. The hosted call bound assumes one call and at most one repair per `runtime.run` and at most `top_n` central
   candidates per seed. Re-checked on phase 2: `runtime.run` still makes the first attempt, one repair and an
   escalation the lab's routing never configures, and `e2_pushdown` still makes one central call per condition and
   candidate (its late collection waits on site answers, not on central calls), so the bound holds.

## 4. Hooks wanted upstream

Each would remove a copy, a private-name dependency or a parse:

- a `runtimes` parameter on `baselines.run_pipeline` (the lab copies `run_pipeline` to pass each site its model
  runtime);
- a public builder of E2's central payloads (the lab mirrors their private shape for the warm-up);
- public channel-block and lift schemas (the lab reads `harness._CHANNEL_BLOCK` and `harness._LIFT`);
- a public statement of E2's capabilities (the lab parses `e2_pushdown._parser()` for `--central-context-tokens`);
- `models_listed` recorded as a boolean (whether the requested model was listed), not the host's whole listing;
- a runtime hook to omit `temperature` for hosts that refuse it;
- a public per-model scoring entry point in `e1_extract` (the report's per-model scores, `endpoint_scores`, call
  its private `_load_pack`, `_read_run` and `_endpoint_block` read-only, as `compare` calls them, so a model is
  scored without a complete reference);
- `not_sent` in a public tuple of extraction error kinds;
- a shared home for the model-name guard: the lab's guard imports
  `tests.mycelic.test_collective_guards.model_name_hits`;
- a public builder of X1's `by_construction` entries (the lab's simulation scorecard writes them in X1's shape from
  `harness.BY_CONSTRUCTION_STATEMENT` and `harness.BY_CONSTRUCTION_LABEL`, so the report shows the harness's own
  label beside the simulation's lifts over S and R_mf);
- an openFDA replay `denominator_note` that names the product codes in the cache: `openfda_replay.DENOMINATOR_NOTE`
  says false alarms count alerts on every product code of the manufacturer, but the lab fetches only the request's
  three to five codes, so the lab's summary states the narrower scope itself (`OPENFDA_FALSE_ALARM_SCOPE`).

## 5. Phase-2 features the lab does not use yet

- E2's chance average precision and chance-corrected ratio: they reach the report inside E2's `verdict` block but
  have no table of their own.
- X1's net finds: phase 2's X1 scorecard carries `found_net` and `chance_found`, and the report's X1 rows copy them,
  but the X1 summary table shows only the raw finds.
- The net recall of X1's `by_construction` entries: phase 2 adds `control_recall` and `recall_net` to each entry; the
  report copies an entry's channel, visibility, label and recall and its channel's units and found for that
  visibility, not those two. The simulation's entries keep the base shape.
- Routing `reasoning_effort` and `chat_template_kwargs` (pinned by E1 in phase 2): they could admit hosted reasoning
  models that the preflight now refuses with `HOSTED_LENGTH`.
- `e5_injection` (E5) and the follow-up work behind X4: not adapted to the lab.

## 6. The research simulator

`research/mycelic` holds two kinds of numbers.

- The benchmark (`experiments.py` and the modules it runs) simulates a synthetic enterprise in numpy. Its
  `models.py` declares model tiers as assumed capability vectors ("assumed placements", in its own words), so the
  benchmark's numbers are not measurements of any model. No module of the package calls a model: there is no
  OpenAI-compatible client, base URL or HTTP call in it.
- Its live measurements are measurements of real models. `live_tasks.py` (five primitive operators, scored by
  `score_live.py`) and `live_rank.py` (candidate discrimination: rank the candidates of a real benchmark run and pick
  the genuine cross-organisational risks, with the evidence statistics only or with the raw work notes too) write a
  task file to `artifacts/` and its answer key to `keys/`. A model's answers go into `artifacts/` as a JSON file
  (`live_rank_answers_<name>.json`, with `ranking` and `real` lists of candidate ids, for `live_rank.py`), and
  `python -m research.mycelic.live_rank score` scores every such file by average precision and selection F1 against
  the key, beside the simulator's own logistic and random ranking. The committed answers came from three hosted
  model tiers pointed at the task file (two runs each for the ranking task; no code in the package made those
  calls); `live_rank_results.json` and `live_rank_results_rich.json` hold their scores, and
  `docs/MYCELIC_ENTERPRISE.md` section 23 reports them. STRATEGY section 11.2 names `live_rank.py` as E2's harness.

The lab runs neither. The lab's E2 is the collective's pushdown harness on a planted world, not `live_rank.py`. The
package's README gives the lack of local inference as the reason its model tiers are assumed positions; the lab now
provides local inference. A lab-side adapter could drive the live measurements without editing `research/**`: send
the committed task file to the shard's model server (or a hosted entry), write the answers file into a scratch copy
of the package, and score it there, since `live_rank.py score` writes its results into the package's own
`artifacts/`, which the lab never writes. That adapter needs a request block, a unit kind and report rows of its
own; it is an open item, not built.

## 7. The judge test's runner

`lab/j1.py` (judge test J001, `docs/collective/replay/vehicles/CHOICE-J001.md`) runs the site verifier's judge on one
record at a time. It lives in the lab and changes nothing under `mycelic/`. It uses these pinned functions read-only:

- `edge/verify.py`: `judge_task` and `judge_schema` (the task, its instructions, its schema and its `max_tokens`),
  `judge_payload` (the payload, with the record's codes empty), `lexical_judge` (the baseline, the verifier's judge when
  no model runs), `decide` (the verdict over the one record: a reply judged, or one failure when no reply passed) and
  `JUDGE_TASK`;
- `edge/records.py`: `WindowRecord`, which holds a labelled record as the verifier holds one of its own (and, holding
  only a record's ref, the record the record-blind control and the aggregate's line check pass to `decide`, which
  counts records and reads replies, never a record's text);
- `edge/extract.py`: `BREAKER_AFTER` and `server_down`, the verifier's own stop after the server is down;
- `inference/runtime.py`: `Runtime.run` (one repair, no escalation in the lab's routing) and `Runtime.exemption`;
  `VALIDATION_KINDS` sorts a failure as the model's (through `e1_extract.failure_class`, and in `lab/units.py`'s
  participation check);
- `experiments/e1_extract.py`: `read_labels`, `check_data_label`, `endpoint_pins` and `failure_class`, so J1 reads,
  checks and pins exactly as E1 does;
- `experiments/common.py`: `measurement_flag`, `code_hash`, `code_files`, `run_dir` and `write_json_atomic`;
- `inference/errors.py`: `KINDS`, the error kinds a stored verdict line may name.

Both experiment modules are in J1's code hash (`lab.j1.CODE_FILES`), beside the edge, pack, inference and stats
modules, so a change to any of them between the plan job and a shard stops the shard before its first call.
- `stats.py`: `cluster_bootstrap_mean`, `paired_bootstrap` and `percentile`.

What `SiteVerifier.answer` adds around these functions is not used: the boundary's closed spec, the daily budgets,
master data, secrets, storage and the pooling of a window of records into one verdict. Those decide whether a
question is judged and over which records, not how one record is judged. A hook wanted upstream: a public verifier
function that judges one record and returns the reply and the verdict, so the lab would not assemble the call itself.

## 8. The latency test's path

`lab/l1.py`, `lab/l1path.py`, `lab/l1score.py` and `lab/l1guard.py` (latency test L001,
`docs/collective/L001/CHOICE-L001.md` with its amendments K1 to K13; the build note is `BUILD-L001.md` beside it) run
the product's own pushdown path, end to end, and change nothing under `mycelic/`, `demo/` or `tools/`. They use these
read-only:

- the drafter demo's steps (`demo/onboard/run_demo.py`: `step_export`, `step_draft`, `mine_labels`), imported as a
  module, and `tools/onboard/fetch_msha.py` (`download`, `split`), so the data are the demo's;
- `pilot/audit.py`: `audit` and its master data, and `evaluate/baselines.py`: `run_pipeline` (enterprise `pilot`);
- `detect/detectors.py`: `detect`, and HQ's store (`save_run`, `candidate`, `key_cells`, `pd_verdicts`, `routes`);
- `pushdown/orchestrator.py`: `Orchestrator` (`verify_stored`, `verify_candidate`, `join_late`, `collect_late`) and
  `constructed_candidate`; `pushdown/gate.py`: `evaluate` and `hq_record_body` for the derived default deadline;
- `edge/verify.py`: `SiteVerifier` (seeded with `demo_seed` 1, its clock at the question's `as_of`), `retrieve`,
  `judge_payload`, `lexical_judge` and `decide`; `edge/site.py`: `EdgeSite` for the stores with the codes hidden;
  `edge/extract.py`: `BREAKER_AFTER` and `SERVER_DOWN_KINDS`, so the unit check reads the verifier's breaker stop as
  the verifier makes it, and `truncate` for the warm-up's worst case;
- `inference/runtime.py`: `Runtime` at boundary `site:<mine>`, one per mine; `inference/client.py`: `list_models`;
- `leakage.py`: `scan`, as E2 scans pushdown's artifacts; `onboard/report.py`, `onboard/draft.py` and
  `onboard/check.py`: the backstop, the refusal's value index and the n-grams the guard scans with;
- `experiments/e1_extract.py`: `endpoint_pins` and `failure_class`; `experiments/e3_latency.py`: `filler` for the
  warm-up's generated record; `stats.py`: `percentile`.

The key judge, the record-blind control and the route-role baseline are lab runtimes with the runtime's surface
(`boundary`, `config`, `exemption`, `run`), since the pinned fake provider is simulated-only and refuses real records.
Each model runtime's `run` is wrapped to keep its reply by the record's index, and each mine's handler is wrapped by a
timer; the calls pass through unchanged. All of these files are in L1's code hash (`lab.l1.CODE_FILES`), so a change
between the plan job and a shard stops the shard before its first call. A hook wanted upstream: a timing callback on
the orchestrator's delivery, so the lab would not wrap the handlers.

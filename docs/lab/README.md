# The Mycelic cloud lab

This guide is for the person who asks for runs and reads their results. Every key, column, status and file is defined
in [REFERENCE.md](REFERENCE.md); the models are described in [MODELS.md](MODELS.md); how the lab fits the collective
layer's current and next code is in [INTEGRATION.md](INTEGRATION.md).

## What the lab is

The lab runs the collective layer's experiment harnesses with real small language models on GitHub-hosted runners. You
write a request (a small JSON file naming experiments, models, seeds, sizes and time budgets) and push it; the
`mycelic-lab` workflow then:

1. plans the run: validates the request, splits it into units (one experiment on one model) and shards (the units one
   runner job executes), and preregisters what E1, X1 and E2 will be judged against before any model runs;
2. downloads and verifies the pinned model server and model files once per run, and caches them;
3. runs each shard on its own runner: starts the model server on the runner's loopback address, warms it up and runs
   the units against it;
4. merges the shards into one report.

Every number in a summary is read from a run file, and each summary starts by saying when nothing in it measures a
model. The experiments:

- E1: extraction quality of the models against labelled records;
- E2: pushdown verification against a central reader, on a planted synthetic world;
- E3: latency and throughput of the model server on the runner;
- G0: the canary leakage scan with the model inside each simulated site;
- sim: the six-site simulation, with the model inside each site, HQ detection, pushdown and a scorecard against the
  baselines;
- X1: the model-free evaluation harness on a plant fixture;
- openFDA: a model-free replay of public openFDA data and the labelling sheets for a person.

## What a run costs

- Nothing on this repository: GitHub Actions is free for public repositories that use standard GitHub-hosted runners
  (GitHub docs, `content/billing/concepts/product-billing/github-actions.md`). A standard Linux runner has 4 CPUs,
  16 GB of memory and 14 GB of SSD (`data/reusables/actions/supported-github-runners.md`).
- Time: a job may run for at most 6 hours (`content/actions/reference/limits.md`). A request's `job_minutes` is
  45 to 330, under that cap; each shard job gets its planned minutes plus the shard overhead.
- Parallel jobs: a request's `max_parallel` is 1 to 16. The account's concurrent-job limit for standard runners is
  20 on Free, 40 on Pro and 60 on Team (`content/actions/reference/limits.md`), and every repository of the account
  shares it, the mycelic CI included, so 16 or fewer leaves room for the rest.
- Caches: a run restores only caches of its own branch or of the default branch; entries not used for 7 days are
  removed; a repository holds 10 GB of caches by default
  (`content/actions/reference/workflows-and-actions/dependency-caching.md`). Run every request on one long-lived
  branch, `lab`, so the model files cached by one run are reused by the next.
- Retention: a request's `retention_days` (1 to 90) is how long its artifacts are kept. The repository's retention
  setting also applies to logs and, since 1 October 2026, to workflow runs, checks and commit statuses
  (`data/reusables/actions/about-artifact-log-retention.md`), so the summaries disappear too. Keep what matters
  (see Keep results).
- Hosted providers: a hosted model (Optional secrets) bills every call on the host's account; GitHub does not.
- openFDA: free; it limits how many requests a client makes. The lab caps a run's openFDA fetch at 100 requests
  without the openFDA key and 1000 with it.

## Everything is public

Everything a run writes is public: anyone can read this public repository's run logs, step summaries and artifacts.

- That includes the request's `purpose`, the plan, the provenance and every unit record.
- The provenance names the runner's CPU model, CPU count and memory, the hashes of the request, manifest, lock, plan,
  server archive and model file, and, for a hosted model, the host's scheme and `host:port`. It never holds the base
  URL's path or a key.
- Never put partner data, real records or secrets in a request. The lab generates its own synthetic data, or reads
  public openFDA data.

## Request a run

1. Copy a template from `lab/templates/` to `lab/requests/<name>.json`. The name must match `[a-z0-9][a-z0-9-]{0,39}`
   (lower-case letters, digits and hyphens). Each template's `purpose` says which name to use.
2. Commit it and push the commit to the branch `lab`, one request per push. Pushing an edited request runs it again;
   pushing the same file again does nothing.
3. The workflow file must be in the pushed commit: the lab's branch carries it.
4. Until the lab is merged to `main`, the first push of this work as the branch `lab` adds
   `lab/requests/plumbing-001.json` and so starts the plumbing run:

   ```
   git push origin mycelic-cloud-lab:lab
   ```

5. Once the lab is merged to `main`, a request can also run by dispatch: Actions, then `mycelic-lab`, then Run
   workflow, with the request's path as the request input; or, with the GitHub CLI, on the branch that holds the
   request:

   ```
   gh workflow run mycelic-lab.yml --ref <branch> -f request=lab/requests/<name>.json
   ```

   Run `lab/requests/plumbing-001.json` on `main` once by dispatch after the merge.

Pushes to main never run a request; on main a request runs only by dispatch.

GitHub's path filter decides whether a push starts the workflow: only a change to `lab/requests/*.json` does, a push
of more than 1,000 commits always does, and the first push of a new branch is compared with the parent of the deepest
commit it pushed (`data/reusables/actions/workflows/triggering-a-workflow-paths5.md`). The plan then applies the same
rule and runs exactly one added or changed request.

Check a request locally before pushing it. The plan step validates the request and writes the plan (exit 0) or names
the field to fix (exit 2):

```
python -m lab.plan --request lab/requests/<name>.json --manifest lab/models.json --out <empty dir>
```

The dry run runs the whole plumbing request on your machine against the collective's fake server; everything it
writes is labelled plumbing:

```
python -m lab.dryrun --request lab/requests/plumbing-001.json --out <empty dir>
```

## The first runs, in order

The pinned server release asset and the model file names in lab/models.json are unverified until the check run downloads and verifies them.
Run the first requests in this order, each after the previous one's report:

1. `lab/requests/plumbing-001.json` (already in the repository): the fake server answers every call. It shows that
   the workflow, the summaries and the report work; nothing in it measures a model.
2. check-001, from `lab/templates/check.json`: the first real run. It downloads and verifies the pinned server
   archive and the smallest model file and times a few short requests. After it, the pins are real.
3. smoke-001, from `lab/templates/smoke.json`: the first model numbers, and the sizing the main run needs for each
   of its models. Every local model of the main run gets latency (E3) and the simulation on the small plant, one
   shard each; `a-0p5b` also gets the canary scan, whose ledgers count the calls a G0 unit makes. A model too slow
   for the simulation's minutes is skipped after its first twenty extraction records and still gets its sizing row,
   projected instead of measured. A skipped unit exits 1 (see [exit codes](REFERENCE.md#exit-codes)), so that model's
   shard job shows red: on the smoke run that is the expected path, and the report still has its sizing row.
4. Commit the lock. Download the report artifact (`lab-report-real-...`) of the smoke run, copy its
   `lock-candidate.json` to `lab/models.lock.json`, commit and push to `lab`. A commit that changes only the lock
   starts no run. When two runs left candidates, put both report folders in one directory and merge them:

   ```
   python -m lab.provision merge-lock --manifest lab/models.json --candidates <dir> --out lab/models.lock.json
   ```

   `lock candidates disagree` means an upstream file changed between the runs: check the provision records before
   pinning either.
5. Set the minutes of the main request before copying it. Larger models are slower on the same runner, and one
   `minutes` value serves every model of a block, so size each block by its slowest model: work out the minutes for
   each model of the block from its own numbers in the smoke report, and use the largest. A model that needs more
   than a shard's capacity (`job_minutes` less the shard overhead of 25 minutes: 305 at 330) cannot run that
   experiment in one job; leave it out of the block's `models`. E1's `reference` cannot be left out: when one of its
   repeats needs more than a shard's capacity, change `reference` or lower `labels.n`.
   - sim: the smoke report's "Simulation sizing for the next request" table gives each model's suggested minutes;
     `experiments.sim.minutes` is the largest among the sim models. Sized from a faster model, a slower one is
     skipped by its projection (`projected {projected} min > budget {budget} min`) and gives no scorecard.
   - G0, an estimate, not a measurement: for each model, ceil(1.25 x (E x its extraction median s + J x its judge
     median s) / 60), plus a few minutes of warm-up, where the medians are the model's in the sizing table and E and
     J are the calls of `extract_claims` and `judge_record` in the smoke report's "Model call latency from the
     ledgers" rows of experiment `g0` (a G0 unit that did not finish leaves no rows: raise its minutes and run the
     smoke again). E and J hold for the smoke's G0 `records` and `seed`, so keep those in the main request. J was
     counted with `a-0p5b`, whose extractions led HQ to its own candidates; another model's can lead to more judge
     calls, which the quarter margin covers only roughly. G0 has no projection: a unit whose minutes are too low runs
     them out and times out.
   - E1, an estimate too: for each E1 model, the reference included, ceil(1.25 x `labels.n` x its extraction median
     s / 60), plus a few minutes of warm-up, per repeat; `experiments.e1.minutes` is the largest. E1 has no
     projection either, and a reference repeat that times out leaves no comparison for any model (`the reference
     model has no complete set of valid repeats, so no comparison was run`).
6. main-001, from `lab/templates/main.json`: the four local models, E1, E2, E3, G0, the simulation and X1.
7. Optional: `lab/templates/openfda-replay.json` (public data, no model) and `lab/templates/hosted-comparison.json`
   (needs the hosted secrets and a manifest entry).

## Cancel or re-run

- Cancel: Actions, the run, Cancel workflow. A cancelled job skips its seal, summary and upload steps (they run only
  when the run was not cancelled) and the aggregate job does not run, so a cancelled run has no report. To keep
  partial results, let the run finish: units that do not fit their time are skipped and recorded.
- Re-run failed jobs: supported. The re-run shards upload artifacts under the new attempt number, and the report
  takes each shard's newest attempt (two artifacts of one attempt for the same shard make it `ambiguous`).

## Read the results

Each run writes three kinds of step summary, shown on the run's page: the plan summary (what will run), one summary
per shard (its runner, server and units) and the report summary (everything merged). Their files are artifacts:

- `lab-plan-<run id>-<attempt>`: the plan, its preregistration and the plan summary;
- `lab-prov-<run id>-<attempt>-<entry>`: one provision record per downloaded file;
- `lab-run-<run id>-<attempt>-<shard>`: one sealed shard: provenance, status, unit records, collected harness files
  and its summary;
- `lab-report-<class>-<run id>-<attempt>`: the report (`report.json`, `report.md`), the lock candidate and E1's
  comparison files; `<class>` is `plumbing` or `real`.

Each summary step also prints into its job log, so a run can be read without downloading an artifact: the plan job
prints the plan summary (`plan/summary.md`); each shard job its `provenance.json` and its summary
(`shard/provenance.json`, `shard/summary.md`); the aggregate job the report's `report.json`, `report.md` and
`lock-candidate.json` (`report/report.json`, `report/report.md`, `report/lock-candidate.json`). Each file stands
between a `=== MYCELIC-LAB <label> BEGIN lines=<n> sha256=<hex> ===` line, which gives its line count and the sha256
of the file, and a `=== MYCELIC-LAB <label> END ===` line; a missing file is a single `ABSENT` line, and an `INDEX`
line, listing each label's line count, comes last. `provenance.json` and `report.json` are printed indented: written
back as canonical JSON (sorted keys, no spaces, a final newline) they are the file again, with the sha256 shown;
`lock-candidate.json` and the summaries are printed as they are. These logs are public like everything else, and
they expire with the repository's log retention.

The first line of a summary says what its numbers are not:

- `PLUMBING_CHECK_LINE`: PLUMBING CHECK: no model was run
- `PLUMBING_HOSTED_LINE`: PLUMBING CHECK: no model was run on this runner, but any hosted unit's calls go to the configured host
- `NO_MEASUREMENT_LINE`: NO MEASUREMENT: no unit here passed every model check, so nothing below measures a model

A plumbing report also carries a banner:

- `PLUMBING_BANNER`: Plumbing check: a fake model answered every call. These records test the lab, not any model.
- `PLUMBING_HOSTED_BANNER`: Plumbing check: a fake model answered every call except any hosted unit's calls, which go to the configured host and are billed by it. These records test the lab, not any model.

The hosted variants mark a plumbing run that names a hosted model (Optional secrets): its hosted units call the
configured host for real, which bills those calls, and their numbers are still filed as plumbing.

A report sorts its units into display classes, each in its own section, and only `model` is a measurement on the
runner:

- `model`: a verified model file served by a verified server on this runner, with every check passing;
- `hosted-api`: a model on the configured hosted provider answered; a result of the host, never a measurement on
  this runner;
- `unverified`: a real run in which some provenance check failed; its class reason says which;
- `plumbing`: a fake answered, or the unit ran in a plumbing run (provider `fake`), where a hosted unit's calls
  still go to the configured host;
- `no-model`: a model-free unit (X1, openFDA);
- `no-result`: the unit did not run to a result.

A unit's status is `ok`, `result_fail` (a valid FAIL verdict, such as a leak found), `invalid` (it ran, but the model
did not take part enough, the server died, or G0 did not test the model in the loop), `failed`, `timed_out`,
`interrupted` or `skipped` (out of budget, or projected over its minutes); the report adds `not_run` (its shard left
no artifact) and `excluded` (its shard's artifact or files could not be trusted). The lock section says `unchanged`,
`new_entries`, `conflict` or `not_computed`.

What each experiment's numbers mean and do not mean. Each label below is printed above its table, where `{e_one}`,
`{e_two}`, `{x_one}` and `{n_one}` stand for E1, E2, X1 and N1:

- E1 scores field F1 against labels. With generator labels it is the extraction fidelity on template text, not the
  decision about real narratives:
- `E1_LABELS.generator_text`: {e_one} here scores extraction against generator ground truth on template text. It is not the STRATEGY {e_one} decision, which needs human-labelled public narratives.
- E2 compares the ranking of pushdown verification with a central reader of raw text, on a planted world:
- `E2_LABELS.synthetic`: {e_two} here runs on planted synthetic worlds; the pack, the detectors and the plant were written by one author, so the plant is not blind. Internal only, never a result to show buyers.
- E3 and the latencies of local models are this runner's:
- `NOTES.runner_hardware`: Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how this runner performed, not what a site would see.
- G0 and the simulation's raw-text scan read text only:
- `NOTES.text_only_scan`: The canary scan covers text only: it reads the bytes that crossed a boundary, not timing, sizes or other side channels.
- The simulation is synthetic and internal:
- `SIM_NOTES.synthetic_internal`: Simulation: a seeded synthetic world with planted patterns, written by the same author as the detectors; internal only, never a result to show buyers.
- X with the model against X with the lexical extractor measures the model's extraction errors, which can lose patterns
  and can also find them; such a find is no sign that the model reads better, and the report's notes say when one
  happened:
- `SIM_NOTES.lexical_exact`: The lexical extractor is exact on generator text by construction, so wherever X with the model and X with the lexical extractor differ, the model's extraction errors made the difference, in either direction: errors can lose a pattern, and they can also find one X with the lexical extractor missed, as when a wrong predicate on an entity the text does name adds counts to a planted key. A find of that kind counts for the model in its lifts over S and over model-free R as well. The difference measures extraction fidelity on synthetic text, not the value of reading real narratives.
- Every plant a request can name has narrative_only patterns, which give S and model-free R nothing to find, so the
  simulation's and X1's lifts over them are partly fixed by the plant. A table under the lifts gives those patterns
  with the collective harness's own label, `by construction, not a result`, after this sentence:
- `BY_CONSTRUCTION_NOTE`: By construction, not a result: planted narrative_only records carry no codes and no structured entities, so they add nothing to the cells S and model-free R read, and a find of theirs on a narrative_only pattern is chance, from background records. Every such pattern X found and they missed counts for X in the lifts over S and over model-free R, so that share of those lifts is fixed by the plant, not measured; only the plant's other patterns compare the channels.
- X1 runs no model at all:
- `X1_LABEL`: {x_one} here runs the model-free evaluation harness on a same-author plant fixture that is not blind; the extractor is lexical and no model runs. It is not STRATEGY's blind {x_one} test.
- openFDA is public data with no model:
- `OPENFDA_LABEL`: Public data, artificial partitioning, not a confidentiality demonstration; no model in this pipeline.
- its false alarms cover only the codes the request fetched:
- `OPENFDA_FALSE_ALARM_SCOPE`: False alarms per week count alerts on the request's product codes only, those in the fetch table, not on every product code of the manufacturer: the lab fetches only the requested codes, whatever the replay's own denominator note says.
- each replay row shows the request's `saw_recall_outcomes` declaration and the replay's warnings; when a requester
  saw recall outcomes first, or the replay warned, the summary says what that means first:
- `OPENFDA_SAW_RECALLS`: The requester declared that they saw recall outcomes before the codes, manufacturers and settings were fixed, so the vocabulary and detector settings were not frozen blind: the recall figures below may reflect hindsight and are not a preregistered result.
- `OPENFDA_WARNED`: The replay warned: a warning can make a channel's zero structural, as when too few sites leave no cross-site candidate, rather than a negative result.
- and its labelling sheets have no result until a person fills them:
- `SHEETS_LABEL`: No labels were generated: {n_one} and openFDA {e_one} have no result until a human labels and commits the sheet.

Every column and its meaning is in [REFERENCE.md](REFERENCE.md#columns), and every label in
[REFERENCE.md](REFERENCE.md#labels). The columns `found net of chance`, `chance finds` and `model path problems`
appear once the collective's phase-2 work is merged: the simulation then runs a no-plant control and G0 checks its
model path.

## Keep results

Artifacts and, since 1 October 2026, the runs themselves expire. To keep a run's results, download its report
artifact and copy `report.json`, `report.md` and `lock-candidate.json` into `lab/results/<request name>/`, then commit
and push to `lab`. Such commits start no run (the workflow watches only `lab/requests/*.json`), the plan never reads
them, and the lab's name guard skips `lab/results/` (a report names model repositories). Kept results are public like
everything else.

## Optional secrets

Three repository secrets, set under Settings, then Secrets and variables, then Actions; without them the lab works
and skips only what needs them:

- `MYCELIC_LAB_OPENFDA_API_KEY` raises the openFDA fetch cap of a run from 100 requests to 1000; it is passed only to
  the shard that holds the openFDA unit.
- `MYCELIC_LAB_HOSTED_BASE_URL` and `MYCELIC_LAB_HOSTED_API_KEY` name one OpenAI-compatible host (`https` to a public
  host, no credentials, query or fragment in the URL) and its key. With both set, a request may use a hosted model as
  an E1 model or as E2's central comparator. Without them, the plan skips those units with a notice and bills nothing.

A hosted model also needs an entry in `lab/models.json` (none ships). Replace each `<...>` with the host's value; the
price lets the report estimate the cost and is optional:

```json
"big-hosted": {"kind": "hosted", "model": "<the host's model id>",
               "context_tokens": <the context one request gets on the host>,
               "response_format": "json_schema",
               "price": {"per_mtok_in": <USD per million input tokens>,
                         "per_mtok_out": <USD per million output tokens>},
               "deadline_s": 300, "max_retries": 2}
```

`context_tokens` is required when the model is E2's central comparator. A request that uses a hosted model names its
`max_calls` in its `hosted` block, and the plan refuses a value below the most calls the request can make on the host
(the bound formula is in [REFERENCE.md](REFERENCE.md#request-format)). `max_calls` caps only the units a shard
starts: a unit that has started makes its calls, so prepaid credits on the host are the hard limit.

Safety:

- The workflow has no pull_request trigger; push and dispatch both need write access to the repository.
- The secrets reach only the shards that need them; the plan job learns only whether they are set.
- Raw synthetic text is sent to the host, never partner data, and hosted models never run in the canary scan or the
  simulation.
- A request with provider `fake` may name a hosted model too: the run is then a plumbing check whose hosted units
  still call the host and are billed; its summaries open with `PLUMBING_HOSTED_LINE`.
- GitHub Models is retired (`content/github-models/index.md`: fully retired on 30 July 2026); the lab has no code for
  it. Use any OpenAI-compatible host.

## What the lab does not do

- It makes no human labels. N1 and the openFDA E1 sheet wait for a person to label and commit them; the lab only
  writes the sheets.
- It does not run `research/mycelic`. Its benchmark is a numpy simulator whose `models.py` holds assumed capability
  vectors, so the benchmark's numbers measure no model; its live measurements (`live_tasks.py`, `live_rank.py`) do
  score real models' answers, but the lab has no adapter for them yet ([INTEGRATION.md](INTEGRATION.md), section 6).
- It has no GitHub Models provider and reads no partner data.
- E5 and the X4 follow-up are not adapted to the lab yet ([INTEGRATION.md](INTEGRATION.md)).

## Troubleshooting

A plan or unit message names the problem; the rows below say what to do. A request error reads
`<json path>: <problem>`: fix that field of the request and push it again. Braces such as `{task}` stand for values
the message fills in.

| message | what to do |
| --- | --- |
| `a push may add or change only one request` | Push each request in its own push, or dispatch the others. |
| `a merge brought several requests; run each one by dispatch` | Run each request with the dispatch commands the plan job printed. |
| `the workflow ran but the push changed no request file` | Nothing to do: the push changed no request (a push of more than 1,000 commits always starts the workflow). |
| `no base commit could be found for this push` | Push the request again in a new commit on `lab`, or dispatch it. |
| `the checked-out commit is not the pushed commit` | A newer push overtook this run; check the newer run. |
| `a request file name must match the request name pattern` | Rename the file to `[a-z0-9][a-z0-9-]{0,39}.json` and push again. |
| `pushes to the default branch run nothing; dispatch the workflow instead` | Push to `lab`, or dispatch the request on `main`. |
| `delete-only push: nothing to run` | Nothing to do: deleting a request runs nothing. |
| `the manifest or lock changed since the plan` | A commit changed the manifest or lock during the run; push the request again. |
| `the release tag does not exist` | The server tag in `lab/models.json` is wrong; fix it (see MODELS.md) and run check again. |
| `the release has no asset with the pinned name` | Fix the server asset name in `lab/models.json` and run check again. |
| `the release asset is not fully uploaded yet; retry later` | Wait and push the request again. |
| `the downloaded file does not match the pinned digest` | The file differs from what GitHub published; run again, and if it persists, do not pin it. |
| `the downloaded file does not match the lock` | The upstream file changed since it was pinned; check it before updating the lock. |
| `the hub refused the metadata request: private, gated, missing or renamed repository` | Check the model's repository in `lab/models.json`. |
| `the hub redirected the metadata request (renamed repository?): update the manifest` | Put the repository's new name in `lab/models.json`. |
| `the hub metadata API stayed unavailable` | Run again later. |
| `the hub's commit differs from the lock` | The locked commit is gone or moved; check the repository, then re-pin. |
| `the repository is gated` | Choose an ungated model; the lab never accepts gated repositories. |
| `the hub's licence differs from the manifest's or is not stated` | Check the licence; the lab accepts only apache-2.0 and mit. |
| `the file is not in the repository at this revision; available .gguf files:` | Set the model's `file` to one of the listed files. |
| `the file is larger than {limit} GiB` | Choose a smaller file. |
| `the hub's file for the locked commit differs from the lock` | Do not pin it: check the repository's history first. |
| `not enough disk: {free} MiB free, {need} MiB needed` | Choose a smaller model file. |
| `not enough memory: {free} MiB available, {need} MiB needed` | Choose a smaller model file. |
| `the download did not finish before its deadline` | Run again; a cached file is reused by the next run on the same branch. |
| `lock candidates disagree` | An upstream file changed between runs; check the provision records before pinning. |
| `provisioning failed for the server` | Read the provision job's record for the server; the shards of that run did not run. |
| `provisioning failed for model` | Read the provision job's record for that model; its shards did not run. |
| `the server binary failed --version` | The release asset is not a binary for this runner; fix the server asset name. |
| `the model server failed to start` | Read the shard's server log in its artifact. |
| `the model server did not become healthy in time` | Run again; if it persists, choose a smaller model. |
| `server settings differ from requested` | The server ignored a setting; check `server_args` in `lab/models.json`. |
| `alias mismatch: the server does not list or return the requested alias` | Check the model's `alias` in `lab/models.json`. |
| `SIGKILL usually means the runner ran out of memory` | Choose a smaller model or lower its context settings. |
| `memory: {available} MiB available after the model loaded, {needed} MiB needed` | Choose a smaller model or lower its context settings. |
| `warm-up: {task} stopped at the token limit` | The model talks too long for the task; choose another model or set `--reasoning off` in its `server_args`. |
| `warm-up: the server returned reasoning content` | Set `--reasoning off` in the model's `server_args`. |
| `context too small: {task} needs {needed} tokens (worst-case prompt {prompt} plus max_tokens {max_tokens}) but a slot holds {slot}` | Raise the model's context setting named in the message. |
| `server unavailable` | The server died twice in the shard; read its log, then run again or choose a smaller model. |
| `not prepared: run lab.shard prepare for this shard first, or run with --provider fake for a plumbing check` | A local command was run in the wrong order; follow the message. |
| `shard budget exhausted` | Raise `job_minutes`, or lower the minutes or sizes of the shard's units. |
| `the model answered too few calls` | The model failed many calls; read the unit's ledgers, then choose another model or format. |
| `no model call was recorded for a required task` | The model was never called for that task; read the unit's logs. |
| `too many failed requests` | E3 saw too many failed requests; lower its concurrency or requests. |
| `unit timed out` | Raise the unit's minutes (see The first runs, in order). |
| `projected {projected} min > budget {budget} min` | Raise the block's minutes to the largest suggested minutes among its models in the sizing table (The first runs, in order, step 5). |
| `model participation below threshold` | The simulation fell back to the lexical extractor too often; read its ledgers. |
| `the pushdown harness stopped with an uncaught error, often a central or site call that failed after its retries; the files it kept are partial` | Run again; if it persists, read the unit's logs. |
| `the preregistration is missing or differs from the plan's` | The plan job's preregistration failed; read its log and push again. |
| `the canary scan found no leak, but its model path had problems, so it did not test the model in the loop` | Read the listed problems and the unit's ledgers; run again against a healthy server. |
| `openFDA stayed unreachable from this runner after the connector's retries` | Run again later. |
| `openFDA kept refusing requests as too many after the connector's retries: add the MYCELIC_LAB_OPENFDA_API_KEY repository secret, lower max_records_per_code or product_codes, or run again later` | Follow the message. |
| `the openFDA fetch was refused: an HTTP error the connector does not retry, or a bad query` | Check the product codes, dates and field names in the request. |
| `secret MYCELIC_LAB_HOSTED_API_KEY or MYCELIC_LAB_HOSTED_BASE_URL not set` | Add both hosted secrets, or remove the hosted model from the request. |
| `secret {name} is not set` | Add the named secret. |
| `secret {name} is blank` | Set the named secret to a value. |
| `secret MYCELIC_LAB_HOSTED_API_KEY is not a printable ASCII token` | Paste the key again without spaces or line breaks. |
| `secret MYCELIC_LAB_HOSTED_BASE_URL is not a base URL the lab accepts: https to a public host (http only to a loopback address), with no credentials, query or fragment` | Set the base URL as the message says. |
| `hosted preflight failed: HTTP {status}; check the key or model id, or switch response_format from json_schema to json_object to none in lab/models.json` | Follow the message. |
| `hosted endpoint unavailable: {detail} after {calls} preflight calls; run the request again later` | Run again later. |
| `hosted preflight: {task} stopped at the token limit; a reasoning model spends the task's small budget thinking, so choose one without reasoning` | Choose a hosted model without reasoning. |
| `max_calls reached: this shard's share of the hosted calls is used up` | Raise `max_calls` in the request's `hosted` block, or lower the request's sizes. |
| `the extraction comparison keeps fewer than two models or loses its reference without its hosted models; secret MYCELIC_LAB_HOSTED_API_KEY or MYCELIC_LAB_HOSTED_BASE_URL not set` | Add both hosted secrets, or use local models only in E1. |
| `the hosted central comparator needs secrets MYCELIC_LAB_HOSTED_API_KEY and MYCELIC_LAB_HOSTED_BASE_URL, which are not set; the lab never replaces it with the model itself` | Add both hosted secrets, or set `e2.central` to `self`. |
| `no artifact: the shard job was cancelled, timed out at its job limit or never started` | Read the shard job's log; raise `job_minutes` if it timed out. |
| `the shard was not sealed: its status file is missing or unreadable` | Read the shard job's log: its seal step failed. |
| `the artifact differs from the file list it was sealed with` | The artifact was changed after its seal; run again. |
| `the artifact belongs to another plan` | Nothing to do: the report ignored it. |
| `two artifacts of the same attempt claim this shard` | Run the request again. |
| `No plan was written: the plan step failed before it could record why; read the plan job's log.` | Follow the message. |
| `No report was written: the aggregate step failed; read the aggregate job's log.` | Follow the message. |
| `the reference model has no complete set of valid repeats, so no comparison was run` | Read why the reference's E1 units failed; raise their minutes or fix the model. |
| `the comparison harness refused the runs; its log is in the report artifact beside the copied runs` | Read the comparison log in the report artifact. |

# Lab reference

Every key, file, status, class, label, column and artifact of the Mycelic cloud lab. The guide is
[README.md](README.md). The code is the authority: each section names the module it summarises.

## Request format

A request is `lab/requests/<name>.json`, `<name>` matching `[a-z0-9][a-z0-9-]{0,39}`, at most 64 KiB of strict JSON
(`lab/request.py`). Every key is required unless a default is given; unknown keys are refused at every level, and a
string of the form `<...>` anywhere is refused as `placeholder: fill in`. A seed is an int in 0 to 2147483647. The
capacity of a shard is `job_minutes` less the shard overhead of 25 minutes; every `minutes` is 1 to that capacity.

| key | values | default |
| --- | --- | --- |
| `schema_version` | 1 | |
| `purpose` | why this run, 1 to 500 characters; public | |
| `provider` | `fake` or the manifest's server program | |
| `models` | 1 to 8 distinct manifest model keys | |
| `job_minutes` | 45 to 330: each shard job's limit | |
| `max_parallel` | 1 to 16: shard jobs at once | |
| `retention_days` | 1 to 90: how long artifacts are kept | |
| `experiments` | at least one of `e1`, `e2`, `e3`, `g0`, `sim`, `x1`, `openfda` | |
| `hosted` | per hosted model key, `{"max_calls": 1..1000000}` | `{}` |

A fake model needs provider `fake`, a gguf model the server provider; a hosted model goes with either. Provider
`fake` makes the whole run a plumbing check; its hosted units still call the configured host, which bills those calls,
and its summaries then say so (`PLUMBING_HOSTED_LINE`, `PLUMBING_HOSTED_BANNER`).

`e1`, extraction F1 of two or more models against labelled records, one unit per model and repeat:

| key | values | default |
| --- | --- | --- |
| `models` | gguf, fake or hosted models of `models`; at least 2 | `models` |
| `minutes` | per repeat unit | |
| `labels` | `source` (`generator` or `fixtures`), `pack` (a built-in pack), and for generator labels only `n` (40 to 2000) and `seed` | |
| `reference` | one of the E1 models | |
| `margin_points` | 1 to 20 | |
| `runs` | 3 to 5 repeats | |
| `seed` | a seed | |
| `bootstrap_b` | 1000 to 20000 | 10000 |

`e2`, pushdown verification against central reading on a planted synthetic world, one unit per model:

| key | values | default |
| --- | --- | --- |
| `models` | gguf or fake models | `models` |
| `minutes` | per unit | |
| `pack` | a built-in pack | |
| `plant` | `plant_<name>`, a fixture of the pack | |
| `seeds` | 1 to 5 distinct seeds | |
| `weeks` | the pack's baseline plus window weeks to 104 | |
| `eval_from` | the pack's window plus minimum history weeks less one, to `weeks` - 1 | |
| `eval_to` | `eval_from` to `weeks` - 1 | |
| `grace_weeks` | 0 to 8 | 4 |
| `tie_salt` | `[A-Za-z0-9._-]{1,64}` | `lab-e2` |
| `detector_author` | 1 to 80 printable characters | `mycelic engineering` |
| `top_n` | 5 to 60 candidates per seed | |
| `min_candidates` | 1 to `top_n` | `top_n` |
| `central` | `self` or a hosted model of `models` whose manifest entry has `context_tokens` | `self` |
| `bootstrap_b` | 1000 to 20000 | 10000 |
| `bootstrap_seed` | a seed | 1 |

`x1`, the model-free evaluation harness, one unit: the keys `minutes`, `pack`, `plant`, `seeds` (1 to 10),
`weeks`, `eval_from`, `eval_to`, `grace_weeks`, `tie_salt` (default `lab-x1`), `detector_author`, `bootstrap_b`
and `bootstrap_seed`, as for `e2`.

`e3` and `g0`, one unit per model, and `sim`, one unit per model and seed:

| block | key | values | default |
| --- | --- | --- | --- |
| `e3` | `models` | a subset of `models`, not hosted | `models` |
| `e3` | `minutes` | per unit | |
| `e3` | `concurrency` | 1 to 4 distinct levels, each 1 to 8, run in order | |
| `e3` | `requests` | 3 to 200 | |
| `e3` | `warmup` | 0 to `requests` - 1 | |
| `e3` | `workloads` | `extraction`, `short` | |
| `e3` | `seed` | a seed | |
| `g0` | `models` | a subset of `models`, not hosted | `models` |
| `g0` | `minutes` | per unit | |
| `g0` | `pack` | a built-in pack | |
| `g0` | `records` | 50 to 2000 | |
| `g0` | `seed` | a seed | |
| `sim` | `models` | gguf or fake models | `models` |
| `sim` | `minutes` | per unit | |
| `sim` | `plant` | `plant_smoke` or `sim_small` | |
| `sim` | `weeks` | the pack's baseline plus window weeks to 52; the plant must fit every seed's world | |
| `sim` | `top_n` | 1 to 60 | |
| `sim` | `seeds` | 1 to 5 distinct seeds | |

`openfda`, the public replay and the labelling sheets, one model-free unit that reaches api.fda.gov:

| key | values | default |
| --- | --- | --- |
| `minutes` | per unit | |
| `pack` | a built-in pack with an openFDA mapping | |
| `product_codes` | 3 to 5 distinct codes of three upper-case letters | |
| `date_from`, `date_to` | calendar dates `YYYYMMDD`; the span must cover enough ISO weeks | |
| `max_records_per_code` | 1 to 25000 | |
| `manufacturers` | 1 to 10 distinct exact spellings, 1 to 120 printable characters each | |
| `manufacturer_field` | a field path, `device[].manufacturer_d_name` | |
| `partition_field` | a field path | |
| `recalling_firms` | as `manufacturers` | `manufacturers` |
| `lookback_weeks`, `post_weeks` | 1 to 104 | 26 |
| `min_partition_coverage` | a number in (0, 1] | 0.5 |
| `tie_salt` | as `e2` | `lab-replay` |
| `saw_recall_outcomes` | `yes` or `no`: did the requester see recall outcomes first | |
| `n1_sheet` | `{"n": 1..1000, "seed"}`: the N1 labelling sheet | none |
| `e1_sheet` | `{"n": 1..2000, "seed"}`: the openFDA E1 labelling sheet | none |

The fetch makes up to `product_codes` x 2 x ceil(`max_records_per_code` / 1000) requests, at most 100 without the
`MYCELIC_LAB_OPENFDA_API_KEY` secret and 1000 with it.

Hosted models run only as E1 models or as `e2.central`. `hosted` is required exactly when one is used and names
exactly those keys, each with `max_calls` at least the request's bound, the most calls it can make on the host:

    bound = [K in e1.models] x (PREFLIGHT_MAX_CALLS + e1.runs x records x 2)
          + [e2.central == K] x len(e2.models) x (2 x PREFLIGHT_MAX_CALLS + 2 x top_n x len(seeds) x 2)

where `records` is `labels.n` for generator labels, else the pack's fixture records, and `PREFLIGHT_MAX_CALLS` is
`lab.hosted`'s preflight calls per canary task and shard. The plan shows the bound and each shard's share.

## Model manifest and lock

`lab/models.json` (`lab/manifest.py`); unknown keys are refused at every level. The top level holds
`schema_version` (1), `hf_base` (the hub's https base URL), `server` and `models`.

`server` (required when a gguf model is listed):

| key | values | default |
| --- | --- | --- |
| `program` | the server program, also the request provider | |
| `tag` | the release tag | |
| `asset` | the release asset, a `.tar.gz` | |
| `url` | `https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>` | |
| `archive_root` | the archive's top directory, or empty | |
| `binary` | the binary's path under `archive_root` | |
| `format` | `tar.gz` | |
| `args` | allowlisted server arguments: `--reasoning on/off/auto`, `--reasoning-budget N`, `--jinja`, `--no-jinja` | |
| `cache_ram_mib` | 0 to 65536 | 1024 |
| `threads` | `physical` or `logical` | `physical` |

`models.<key>`, a key matching `[a-z0-9][a-z0-9-]{0,23}` (`none` and `hosted` are reserved), of one of three
kinds:

| kind | keys |
| --- | --- |
| fake | `kind`, `alias`, `persona` (default `valid`), `response_format` (`json_schema` only), `transport_schema` (`full` or `reduced`, default `full`) |
| gguf | `kind`, `alias`, `gguf`, `response_format` (`json_schema`, `json_object` or `none`, default `json_schema`), `transport_schema`, `ctx_per_slot` (default 16384), `e3_ctx_per_slot` (default 4096), `e2_ctx_per_slot` (default 32768), each 2048 to 131072, and `server_args` (allowlisted, default none) |
| hosted | `kind`, `model` (the host's id), `response_format`, `transport_schema`, `price` (optional: `per_mtok_in` and `per_mtok_out`, USD per million tokens, 0 to 1000), `deadline_s` (10 to 3600, default 300), `max_retries` (0 to 5, default 2) and `context_tokens` (optional: the context one request gets on the host, 2048 to 2000000; required for an E2 central comparator) |

A gguf entry's `gguf` holds `repo` (`<owner>/<name>`), `file` (a `.gguf` path), `revision` (a 40-hex commit or a
branch) and `license` (`apache-2.0` or `mit`).

The lock is `lab/models.lock.json`, the manifest's sibling: `schema_version` (1), `server` (null or `tag`, `asset`
and `sha256`) and `models` (per gguf key `repo`, `file`, `revision`, `commit`, `sha256` and `size`). A lock entry must
match the manifest's tag, asset, repository, file and revision; a missing entry is trusted on first use and pinned
by the lock candidate a run writes.

Cache keys are derived from a verified file's sha256 (`sha16` is its first 16 hex digits): the server archive is
cached as `lab-server-<tag>-<sha16>` under `server/<tag>`, a model file as `lab-gguf-<key>-<sha16>` under
`gguf/<key>`.

## Ids

- A unit id is `<experiment>-<model>` (E2, E3, G0), `e1-<model>-r<k>` (one E1 repeat), `sim-<model>-s<seed>` (one
  simulation seed), `x1` or `openfda`; at most 55 characters.
- A run id is the unit id, a hyphen and the first 8 hex digits of sha256(request sha256 `|` unit id); the harness
  writes under it.
- A shard id is `s<NNN>-<label>`: a three-digit number in creation order and the shard's model key, `none` for
  model-free shards or `hosted` for the hosted shard.

## Shard root

A shard's artifact holds (`lab/shard.py`):

- `provision/` and `server/`: the prepared server and model records, and the server's logs;
- `routing/`: the routing files the harnesses read (a hosted base URL is redacted);
- `units/<unit>/`: `unit.json` (the unit record), `stdout.log` and `stderr.log` (each cut to its last 64 KiB);
- `runs/<experiment>/<run id>/`: the harness files the lab keeps (result files and ledgers; never a database, a
  private directory or raw scratch);
- `provenance.json` and `status.json` (below); `summary/`, added by the summary step after the seal.

## Provenance and status files

`provenance.json` (`lab.shard.PROVENANCE_KEYS`), rewritten before the first unit and after each one:

| key | holds |
| --- | --- |
| `schema_version` | 1 |
| `kind` | `lab_provenance` |
| `shard` | the shard id |
| `complete` | whether the shard finished writing it |
| `interrupted` | whether a signal stopped the shard |
| `result_class` | `plumbing` when a fake served the shard, else `real` |
| `banner` | the plumbing banner (`PLUMBING_HOSTED_BANNER` when the shard holds hosted units), or null |
| `plan` | the plan's sha256 and path |
| `request` | the request's path, name and sha256 |
| `manifest_sha256` | the manifest's sha256 |
| `lock_sha256` | the lock's sha256 |
| `git` | the plan's commit, the checked-out commit and whether `lab/` was dirty |
| `code` | the collective's code stamps and the lab's code hash |
| `host` | the runner: CPU model, CPUs, memory, OS, Python |
| `provider` | the shard's provider |
| `server` | the pinned server archive, how it was verified, and every server start; `{"kind": "none"}` without a model, `{"kind": "hosted"}` for the hosted shard |
| `model` | the pinned model file and commit |
| `provision` | the provision records' hashes |
| `prereg` | the preregistration's sha256, or null |
| `openfda` | whether the openFDA key was present, or null |
| `hosted` | each hosted secret's state, the scheme and `host:port`, and per key its share, calls, listing and preflight; null without hosted units |
| `deadline_epoch` | the shard's deadline |
| `started_at` | when the shard started |
| `finished_at` | when it finished |
| `wall_s` | its wall seconds |
| `planned_units` | the units the plan gave it |
| `units` | each unit's status and class |
| `exit_code` | the shard's exit code |

`status.json` (`lab.shard.STATUS_KEYS`), written by the seal:

| key | holds |
| --- | --- |
| `schema_version` | 1 |
| `kind` | `lab_shard_status` |
| `shard` | the shard id |
| `out_existed` | whether the shard root existed before the seal |
| `run_attempt` | the GitHub run attempt |
| `plan_sha256` | the plan's sha256 |
| `steps` | the workflow step outcomes |
| `failed_step` | the first failed or cancelled step |
| `prepare` | the prepare step's evidence and problem |
| `provenance` | whether provenance is present and complete |
| `units` | each unit's status |
| `missing_units` | planned units without a record |
| `counts` | units per status |
| `files` | the sha256 and size of every sealed file |
| `sealed_at` | when the seal ran |

## Statuses

A unit's status (`lab.units.STATUSES`):

| status | means |
| --- | --- |
| `ok` | the harness ran to a valid result and the model answered enough |
| `result_fail` | a valid FAIL verdict: G0's canary scan or the simulation's raw-text scan did not pass (something crossed, or the scan's positive control found nothing) |
| `invalid` | it ran, but the model answered too few calls, the server died, or G0's model path had problems |
| `failed` | the harness failed, refused its configuration or wrote no readable result |
| `timed_out` | the unit ran out of its minutes |
| `interrupted` | a signal stopped it |
| `skipped` | it did not start: the shard's budget, the server, the warm-up, a projection or the hosted gate |
| `not_run` | (report only) its shard left no artifact, or its sealed shard has no record of it |
| `excluded` | (report only) its shard's artifact or files could not be trusted |

A shard's artifact in the report is `sealed` (used), `unsealed` (no readable status file), `altered` (a file differs
from the sealed list), `other_plan` (another plan's), `ambiguous` (two artifacts of the newest attempt) or
`no_artifact`.

## Classes

A report's display classes (`lab.units.DISPLAY_CLASSES`): `model` (a measurement on the runner: a verified model
file served by a verified server, every check passing), `hosted-api` (a result of the configured host, never a
measurement on the runner), `unverified` (a real run whose checks did not all pass), `plumbing` (a fake answered, or
the unit ran in a plumbing check, where a hosted unit's calls still go to the configured host), `no-model` (a
model-free unit) and `no-result` (no result).

A unit record's measurement class is `model`, `hosted-api`, `unverified`, `plumbing` or `no-model`, with a class
reason from `CLASS_REASONS` (below). A plan's and a report's result class is `plumbing` (provider `fake`) or `real`.

A provision record's `verified_by` is `lock` (the lock's sha256), `hf-api` (the hub's published sha256),
`github-api` (the release's published digest) or `first-use` (no digest was published or reachable: trusted on first
use, and only a server archive may be).

## Exit codes

The same for every lab command (`lab/__init__.py`):

| code | means |
| --- | --- |
| 0 | done; every unit ran to a valid result (a FAIL verdict is a valid result), or nothing to run |
| 1 | a unit is invalid, failed, timed out, interrupted or skipped |
| 2 | usage or configuration error: a bad request, manifest, lock, event, plan or argument |
| 3 | network error: a download or API that stayed unreachable, or a download past its deadline |

## Labels

Every fixed sentence the summaries print comes from `lab/notes.py`. Braces stand for values the summary fills in:
`{e_one}`, `{e_two}`, `{x_one}` and `{n_one}` are E1, E2, X1 and N1.

The first lines and the plumbing banner:

- `PLUMBING_BANNER`: Plumbing check: a fake model answered every call. These records test the lab, not any model.
- `PLUMBING_HOSTED_BANNER`: Plumbing check: a fake model answered every call except any hosted unit's calls, which go to the configured host and are billed by it. These records test the lab, not any model.
- `PLUMBING_CHECK_LINE`: PLUMBING CHECK: no model was run
- `PLUMBING_HOSTED_LINE`: PLUMBING CHECK: no model was run on this runner, but any hosted unit's calls go to the configured host
- `NO_MEASUREMENT_LINE`: NO MEASUREMENT: no unit here passed every model check, so nothing below measures a model

The experiments' labels:

- `X1_LABEL`: {x_one} here runs the model-free evaluation harness on a same-author plant fixture that is not blind; the extractor is lexical and no model runs. It is not STRATEGY's blind {x_one} test.
- `OPENFDA_LABEL`: Public data, artificial partitioning, not a confidentiality demonstration; no model in this pipeline.
- `OPENFDA_PUBLIC_FLAG`: The replay's measurement flag says only that both caches came from the public openFDA host; it measures no model.
- `OPENFDA_SAW_RECALLS`: The requester declared that they saw recall outcomes before the codes, manufacturers and settings were fixed, so the vocabulary and detector settings were not frozen blind: the recall figures below may reflect hindsight and are not a preregistered result.
- `OPENFDA_WARNED`: The replay warned: a warning can make a channel's zero structural, as when too few sites leave no cross-site candidate, rather than a negative result.
- `OPENFDA_FALSE_ALARM_SCOPE`: False alarms per week count alerts on the request's product codes only, those in the fetch table, not on every product code of the manufacturer: the lab fetches only the requested codes, whatever the replay's own denominator note says.
- `SHEETS_LABEL`: No labels were generated: {n_one} and openFDA {e_one} have no result until a human labels and commits the sheet.
- `G0_BELOW_PROTOCOL`: Some canary scans used fewer records than the protocol scan, whose size the protocol records column gives: they are smaller checks, not the protocol scan.
- `G0_MODEL_PATH`: the canary scan found no leak, but its model path had problems, so it did not test the model in the loop
- `E1_ENDPOINT_EXCLUDED`: A model without a complete set of valid repeats was left out: the comparison is stamped incomplete and lists it among the endpoints without runs.
- `E1_VERDICTS_WITHHELD`: Verdicts withheld: non-inferiority and the kill flag are shown only for a model measurement, beneath the label above.
- `E1_HOSTED_LABEL`: Hosted endpoints answered {e_one} over the network: their scores are not deterministic across repeats, and raw synthetic text was sent to the configured host.

Sizing and cost:

- `SIZING_NOTE`: Sizing: the per-call medians measured on this runner and the minutes they suggest for the next request's sim block, a quarter above the estimate and rounded up; a skipped or timed-out unit's estimate is projected, not measured. One minutes value serves every model of a block, so the block needs the largest suggestion among its models.
- `E2_SIZING_NOTE`: Pushdown sizing: the model-free rehearsal's call counts times this runner's warm-up latencies, against the share of the unit's budget the projection may use; a projection above it skips the unit, and the suggested minutes are a quarter above the projection, rounded up.
- `HOSTED_COST_NOTE`: Estimated cost: the token counts the host reported times the manifest's prices. The host's bill is the real cost; calls without token counts are left out, and max_calls caps only the units a shard starts, so prepaid credits are the hard limit.

Worlds and CPUs:

- `SIM_WORLD_SAME`: Simulation units of the same plant, seed and weeks saw the same synthetic world.
- `SIM_WORLD_DIFFERS`: Simulation units of the same plant, seed and weeks saw different synthetic worlds; their results are not comparable.
- `WORLD_DIGEST_SAME`: Canary scans of the same pack, pack version, seed and size saw the same synthetic world.
- `WORLD_DIGEST_DIFFERS`: Canary scans of the same pack, pack version, seed and size saw different synthetic worlds; their results are not comparable.
- `CPU_MODELS_DIFFER`: The shards ran on different CPU models: compare timings only between rows of the same CPU model.

The lock:

- `NOT_PINNED`: not pinned: trusted on first use
- `LOCK_UNCHANGED`: The lock already pins every file this run verified.
- `LOCK_NEW`: This run verified files the lock does not pin yet: copy lock-candidate.json from the report artifact to lab/models.lock.json and commit it, so later runs verify against it.
- `LOCK_CONFLICT_NOTE`: A verified file disagrees with the lock: the upstream file changed or the lock is wrong; check the provision records before re-pinning.
- `LOCK_NOT_COMPUTED`: No lock candidate was computed: the manifest or lock changed since the plan, or the provision records were ambiguous.

A unit's notes, printed once under the report's notes:

- `NOTES.plumbing`: Plumbing: a fake model answered; nothing here measures a model.
- `NOTES.plumbing_hosted`: Plumbing: this unit's hosted calls went to the configured host and any other call to a fake model; it ran in a plumbing check, so nothing here measures a model.
- `NOTES.synthetic`: Synthetic data: every record and narrative was generated from a seed; no real record was used.
- `NOTES.runner_hardware`: Latency was measured on a shared GitHub-hosted runner, not on site hardware; it says how this runner performed, not what a site would see.
- `NOTES.text_only_scan`: The canary scan covers text only: it reads the bytes that crossed a boundary, not timing, sizes or other side channels.
- `NOTES.model_measurement`: Model measurement: a verified model file answered through a verified server on one shared GitHub-hosted runner; quality numbers describe this model on synthetic data, and timings describe this runner, not site hardware.
- `NOTES.public_data`: Public data: openFDA records under an artificial partitioning; no model ran, and nothing here is confidential or a confidentiality demonstration.
- `NOTES.hosted_api`: Hosted API: a model on the configured OpenAI-compatible host answered over the network. It is not deterministic: temperature and seed are requests, not guarantees on shared batched servers. Its latencies include the network and the host's queue and say nothing about this runner.
- `NOTES.hosted_raw`: Raw synthetic text was sent to the configured host under allow_external_raw synthetic; the lab sends no partner data anywhere, and hosted models never run in the canary scan or the simulation.
- `NOTES.central_hosted`: The central comparator was a hosted API model: its scores are not deterministic, so the ratio and the bar verdict compare the model under test with a moving reference.

The simulation's notes; exactly one of `no_control` and `chance_control` is present, and `model_beyond_exact` only when,
in the lifts' count, X with the model found a pattern X with the lexical extractor missed (a report lists the units
that carry a note some sim rows lack):

- `SIM_NOTES.synthetic_internal`: Simulation: a seeded synthetic world with planted patterns, written by the same author as the detectors; internal only, never a result to show buyers.
- `SIM_NOTES.lexical_exact`: The lexical extractor is exact on generator text by construction, so wherever X with the model and X with the lexical extractor differ, the model's extraction errors made the difference, in either direction: errors can lose a pattern, and they can also find one X with the lexical extractor missed, as when a wrong predicate on an entity the text does name adds counts to a planted key. A find of that kind counts for the model in its lifts over S and over model-free R as well. The difference measures extraction fidelity on synthetic text, not the value of reading real narratives.
- `SIM_NOTES.model_beyond_exact`: In the lifts' count, X with the model found a pattern X with the lexical extractor missed: the lexical extractor is exact here, so the model's extraction errors made that find. It is no sign that the model reads better, yet it raises the model's lifts over S and over model-free R as well as its lift over the lexical extractor; the scorecard's patterns block gives each channel's raw outcome per pattern, before any chance correction.
- `SIM_NOTES.few_patterns`: Few planted patterns: recall, average precision and the lift intervals rest on a handful of patterns and one seed, so they are not interpretable as estimates.
- `SIM_NOTES.r_model_free`: R here is model-free: the same detectors over the fields allowed to leave, with no model; it is not the strategy's R.
- `SIM_NOTES.no_control`: No no-plant control ran: a pattern the channel would also have found without the plant counts as found, and the lifts compare raw finds.
- `SIM_NOTES.chance_control`: A no-plant control ran: the same seed's world without the plant went through the same pipelines, and a find the control made as early is a chance find; the net counts and the lifts leave chance finds out. The control's model extractions were replayed from the planted run for the records both worlds share.

The simulation's channels:

- `SIM_CHANNEL_LABELS.X_model`: X with the model: each site's in-boundary model extracted claims from its own narratives; HQ ran the detectors over the codes and text-only cells that left the sites, k-suppressed
- `SIM_CHANNEL_LABELS.X_lexical`: X with the lexical extractor: the same pipeline with the pack's lexicon in place of the model; exact on generator text by construction
- `SIM_CHANNEL_LABELS.S`: S: codes cells only (no model, no narrative); the same detectors over the same k-suppressed cells
- `SIM_CHANNEL_LABELS.R_mf`: R, model-free: the same detectors over record-level, unsuppressed counts of the fields the pack allows to leave (codes and structured ids, never narrative); not the strategy's R, which may read those fields with a frontier model
- `SIM_CHANNEL_LABELS.U`: U, a reference: every claim of the model channel at record level, unsuppressed, with exact roots and reporters; not a deployable system
- `SIM_CHANNEL_LABELS.single_site`: single site: each site alone, a per-site exceedance test over its own exact counts, sharing the same weekly alert budget
- `SIM_CHANNEL_LABELS.rules`: rules: the pack's hand-written rules over the model channel's cells (episode starts, unranked)

The simulation's lifts:

- `SIM_LIFT_LABELS.X_model_minus_S`: patterns X with the model found minus patterns S found, bootstrapped over patterns
- `SIM_LIFT_LABELS.X_model_minus_R_mf`: patterns X with the model found minus patterns model-free R found, bootstrapped over patterns
- `SIM_LIFT_LABELS.X_model_minus_X_lexical`: patterns X with the model found minus patterns X with the lexical extractor found, bootstrapped over patterns; the lexical extractor is exact on generator text, so the model's extraction errors decide every pattern only one of them found: below zero they lost more patterns than they found, above zero they found more than they lost, and none of it is reading better

Baselines a plant blinds, under the simulation's and X1's lifts whenever a plant has a narrative_only pattern (every
plant a request can name has one: `sim_small` three of four, `plant_smoke` all three). The label is the collective
harness's own (`harness.BY_CONSTRUCTION_LABEL`), carried from each scorecard's `by_construction` entries:

- `BY_CONSTRUCTION_LABEL`: by construction, not a result
- `BY_CONSTRUCTION_NOTE`: By construction, not a result: planted narrative_only records carry no codes and no structured entities, so they add nothing to the cells S and model-free R read, and a find of theirs on a narrative_only pattern is chance, from background records. Every such pattern X found and they missed counts for X in the lifts over S and over model-free R, so that share of those lifts is fixed by the plant, not measured; only the plant's other patterns compare the channels.

Why a simulation is not a measurement:

- `SIM_MEASUREMENT_REASONS.fake_model`: a fake model answered: a fake endpoint, a fake model listing or a fake-server marker
- `SIM_MEASUREMENT_REASONS.low_participation`: the share of records the lexical fallback extracted was above the threshold
- `SIM_MEASUREMENT_REASONS.skipped_projection`: the run stopped after the projection check, before any result

E1's labels, by label source:

- `E1_LABELS.generator_text`: {e_one} here scores extraction against generator ground truth on template text. It is not the STRATEGY {e_one} decision, which needs human-labelled public narratives.
- `E1_LABELS.fixtures`: {e_one} here scores extraction against the pack's author-written fixture records. It is not the STRATEGY {e_one} decision, which needs human-labelled public narratives.

E2's labels:

- `E2_LABELS.synthetic`: {e_two} here runs on planted synthetic worlds; the pack, the detectors and the plant were written by one author, so the plant is not blind. Internal only, never a result to show buyers.
- `E2_LABELS.below_protocol`: Below the protocol minimum of candidates and seeds: the conditions and the ratio are not interpretable as estimates.
- `E2_LABELS.self_central`: The central comparator is the model under test itself, so the ratio compares the model with itself; the bar verdict is not shown.

A unit's class reasons:

- `CLASS_REASONS.fake_kind`: the manifest entry is a fake model
- `CLASS_REASONS.provider_override`: the shard ran with the fake provider in place of the model server
- `CLASS_REASONS.fake_marker`: a response carried the fake-server marker
- `CLASS_REASONS.no_model`: the unit uses no model
- `CLASS_REASONS.no_evidence`: no provisioning or server evidence was recorded for the unit
- `CLASS_REASONS.model_verified`: the model file was not verified against the hub or the lock, or its record changed
- `CLASS_REASONS.server_verified`: the server archive was not verified, or its record changed
- `CLASS_REASONS.download_hosts`: a file came from a host other than the one named, or from a private address
- `CLASS_REASONS.model_path`: the server did not report the verified model file as the one it loaded
- `CLASS_REASONS.ledger_host`: a model call went to a host other than the started server
- `CLASS_REASONS.model_served`: a reply named a model other than the requested alias
- `CLASS_REASONS.harness_measurement`: the harness itself did not count the run as a measurement
- `CLASS_REASONS.participation`: the unit did not run to a valid result
- `CLASS_REASONS.verified`: every provenance condition held: a verified model file served by a verified server on this runner
- `CLASS_REASONS.hosted_host`: a hosted call went to a host other than the configured one
- `CLASS_REASONS.hosted_verified`: the configured host answered every call: a hosted API result, not a measurement on this runner

## Columns

Every column of every summary table (`lab.notes.COLUMNS`). Numbers on this runner describe this runner; numbers on
synthetic worlds describe synthetic worlds.

| column | means | does not mean |
| --- | --- | --- |
| Request | the request file's path, name and short sha | n/a: not a number |
| name | the request's name, its file name without .json | n/a: not a number |
| sha | the first twelve hex digits of a sha256 | n/a: an identifier, not a number |
| Purpose: | the request's purpose, as written; public | a description of what the run found |
| Provider | fake (the plumbing server) or the manifest's server program | n/a: not a number |
| Commit | the git commit the plan or report was made from | n/a: not a number |
| Plan | the first twelve hex digits of the plan file's sha256 | n/a: not a number |
| Job minutes | the request's job_minutes: a shard holds this many minutes less the shard overhead | how long the run took |
| Shards at once | the request's max_parallel: shard jobs that may run at the same time | how many ran at once |
| Days kept | the request's retention_days: how long the run's artifacts are kept | how long logs, summaries or the run itself are kept |
| Source | which file a plan error is about: request, manifest, lock, event or plan | n/a: not a number |
| Path | the JSON path of the refused value | n/a: not a number |
| Problem | why the value was refused, or a lock or hosted problem | n/a: not a number |
| Notice | why a push ran nothing | n/a: not a number |
| Requests | the request files a merge brought, each to run by dispatch | n/a: not a number |
| model | the model's key in lab/models.json | n/a: not a number |
| kind | the manifest kind: gguf, fake, hosted, or none for a model-free shard | n/a: not a number |
| alias | the name the server serves the model under; for a hosted model, the host's model id | n/a: not a number |
| repository | the model's hub repository | n/a: not a number |
| file | the model file in that repository | n/a: not a number |
| revision | the manifest's revision: a commit or a branch | the commit a run used (see Model commit) |
| pin | the locked commit and file sha, or the not-pinned sentence | that the file was downloaded |
| shard | the shard id | n/a: not a number |
| units | the unit ids a shard runs, in order | n/a: not a number |
| planned minutes | the sum of the shard's units' minutes from the request | how long the shard ran |
| job timeout minutes | the planned minutes plus the shard overhead: the job's time limit | how long the job ran |
| unit | the unit id | n/a: not a number |
| experiment | e1, e2, e3, g0, sim, x1 or openfda | n/a: not a number |
| minutes | the unit's minutes from the request: its time budget | how long it ran |
| seed | the unit's (first) seed | a quantity: seeds only select a synthetic world |
| entry | the provision entry: server, or gguf- and the model key | n/a: not a number |
| cache key | the actions/cache key the provision job restores | n/a: not a number |
| Failed step | the first workflow step of the shard job that failed or was cancelled | n/a: not a number |
| Prepare problem | why the shard's server or model could not be prepared | n/a: not a number |
| Provenance complete | whether the shard's provenance file was completed after its last unit | that the units passed |
| Interrupted | whether the shard was stopped by a signal | a failure of the model |
| Units by status | how many units of the shard ended in each status | measurements |
| Run attempt | the GitHub run attempt that produced the shard | n/a: not a number |
| Planned units without a record | planned units that left no unit record | n/a: not a number |
| CPU | the runner's CPU model name | the hardware a site would use |
| CPUs available | the CPUs the shard's process could use on the runner | a site's CPU count |
| Memory GiB | the runner's total memory | the memory a model needed |
| OS | the runner's operating system | n/a: not a number |
| Python | the runner's Python version | n/a: not a number |
| Server tag | the pinned server release tag | n/a: not a number |
| Server version | the server binary's own --version line | n/a: not a number |
| Server archive sha | the first twelve hex digits of the server archive's sha256 | n/a: not a number |
| verified by | how a file was verified: lock, hf-api, github-api or first-use | that a first-use file is trustworthy |
| Model repository | the hub repository of the served model file | n/a: not a number |
| Model file | the served model file | n/a: not a number |
| Model commit | the hub commit the model file came from | n/a: not a number |
| Model file sha | the first twelve hex digits of the model file's sha256 | n/a: not a number |
| status | the unit's status (see Statuses) | a verdict on the model |
| class | the unit's display class (see Classes) | n/a: not a number |
| exit code | the harness's or shard's exit code (see Exit codes) | a model result |
| wall seconds | seconds from the unit's or shard's start to its end on the runner, setup included | model latency alone |
| reason | why a unit has its status, or why a channel has no alerts | n/a: not a number |
| state | the shard artifact's state (see Statuses) | n/a: not a number |
| attempt | the run attempt of the artifact the report used | n/a: not a number |
| failed step | the first failed or cancelled step of the shard job | n/a: not a number |
| workload | the E3 workload: extraction or short, synthetic filler text | a real document |
| concurrency | how many requests E3 sent at once | how many users a site has |
| measured | requests timed after the warm-up requests | requests that succeeded |
| ok | measured requests that succeeded | that their answers were correct |
| end to end median s | the median seconds from sending a request to its last token, on this runner | what a site's hardware would see |
| end to end ninety-fifth percentile s | the ninety-fifth percentile of those seconds, on this runner | a worst case |
| first token median s | the median seconds to the first streamed token, on this runner | what a site's hardware would see |
| decode tokens per s median | the median output tokens per second after the first token, on this runner | throughput under other loads |
| requests per s | successful requests per second over the cell's wall time, on this runner | a capacity planning number for a site |
| harness measurement | whether the harness itself counted its run as a measurement | that the lab's provenance checks passed (see class) |
| pack | the domain pack the synthetic world was generated from | n/a: not a number |
| records | records in the run: generated plus planted, or the scan's size | real records |
| passed | whether the canary scan passed: nothing crossed and its positive control found its canaries | that no leak is possible: the scan reads text only |
| canaries planted | canary strings planted in the synthetic records | real secrets |
| canary hits | planted canaries found in what crossed a boundary | every possible leak |
| shingle overlap bytes | bytes of narrative text found, as shingles, in what crossed a boundary | leaks through timing, sizes or counts |
| positive control hits | canaries the scan found in a site's own database, proving it can find them | a leak |
| model path problems | how many problems G0 found in its model path (no model answer, degraded verdicts) | a leak |
| world digest | the first twelve hex digits of the synthetic world's digest | n/a: not a number |
| task | the model task the calls were for | n/a: not a number |
| calls | successful model calls in the ledgers | every call made |
| median ms | the median latency of those calls in milliseconds | a site's latency |
| ninety-fifth percentile ms | the ninety-fifth percentile latency of those calls in milliseconds | a worst case |
| target | what a provision record is for: the server or a gguf model | n/a: not a number |
| verified | whether the file matched its pin or published digest | that a first-use file is safe |
| first use | whether the file was trusted on first use, with no published digest to check | that it was verified |
| plan matches | whether the provision record belongs to this run's plan | n/a: not a number |
| seconds | seconds the provision step took | download speed elsewhere |
| Lock status | unchanged, new_entries, conflict or not_computed | n/a: not a number |
| New entries | files this run verified that the lock does not pin yet | n/a: not a number |
| CPU models | every CPU model the run's shards ran on | comparable timings across models of CPU |
| Artifacts of shards not in the plan | artifacts the report ignored | n/a: not a number |
| Skipped by the plan | units the plan removed, with the reason | failed units |
| digests | the world digests a group of units saw | n/a: not a number |
| consistent | whether a group of units saw one world | that their results agree |
| Units | units in the plan | n/a: not a number |
| Shards | shards in the plan | n/a: not a number |
| channel | the detection channel (see the channel labels) | n/a: not a number |
| found | planted patterns the channel found in their window, chance finds included | patterns found because of the plant |
| found net of chance | finds the no-plant control did not also make as early or earlier | finds that would hold on real data |
| chance finds | finds the no-plant control also made as early or earlier | false alarms |
| patterns | planted pattern units: patterns times seeds | real problems |
| recall | found over patterns | recall on real data |
| precision in the top forty | the share of true finds among the channel's forty highest-ranked alerts | precision on real data |
| average precision | average precision of the channel's alert ranking against the planted labels | precision on real data |
| alerts | alerts the channel raised in the evaluation weeks | a count of real problems |
| false alarms | alerts that matched no planted pattern window | alerts that are wrong on real data |
| lift | which lift: the difference between two channels' finds | n/a: not a number |
| estimate | the bootstrapped mean of a lift or ratio | an estimate when patterns are few |
| interval low | the low end of the bootstrap interval | a guarantee |
| interval high | the high end of the bootstrap interval | a guarantee |
| candidates | candidates verified by pushdown, or E2's candidates in all | true patterns |
| true | candidates whose key is a planted pattern in its window | real problems |
| supported | candidates pushdown ended as supported | that they are true |
| pushdown average precision | average precision of the pushdown score over the verified candidates | a result on real data |
| detector-score average precision | average precision of the detector's own score over the same candidates | a result on real data |
| raw text bytes crossed | narrative bytes found in what crossed the simulation's boundaries | leaks through timing, sizes or counts |
| lexical fallback share | the share of records the lexical extractor read because the model failed | the model's accuracy |
| records done | records the simulation extracted before it stopped or ended | n/a: not a number |
| extraction median s | the median seconds of one extraction call on this runner | a site's latency |
| judge median s | the median seconds of one judge call on this runner | a site's latency |
| estimated minutes | the run's estimated minutes: measured for a complete run, projected for a skipped one | a promise for the next run |
| suggested minutes | the minutes to request next time: a quarter above the estimate, rounded up | a guarantee that the next run fits |
| plant | the plant spec that planted the patterns | n/a: not a number |
| weeks | weeks of the synthetic world | n/a: not a number |
| labels | the E1 labels: their source, records and claims | human labels |
| claims | labelled claims in the E1 labels | n/a: not a number |
| prereg sha | the first twelve hex digits of a preregistration file's sha256 | n/a: not a number |
| Model-free rehearsal | E2's rehearsal with fake judges, which sizes the run before any model runs | a model result |
| site judge calls | judge calls the rehearsal made at the sites | calls a model made |
| most records in one central reading | the largest number of records one central reading held | a model result |
| field F one | micro F1 over (record, field, value) of the model's claims against the labels, pooled over repeats | accuracy on real narratives |
| claim F one | F1 over whole claims against the labels, pooled over repeats | accuracy on real narratives |
| valid JSON share | the share of extraction calls whose first reply was valid | accuracy |
| model mismatch | whether a reply named a model other than the requested one | n/a: not a number |
| against | the reference model of a paired comparison | n/a: not a number |
| decision metric | which difference decides non-inferiority: micro field F1, or the per-record mean field F1 of a harness without it | n/a: not a number |
| difference | the model's decision metric minus the reference's over the shared records | a difference on real narratives |
| mean difference | the mean per-record field F1 difference over the shared records | the decision when the decision metric is micro field F1 |
| sign test p | the exact sign test's p-value of the per-record differences | an effect size |
| underpowered | whether fewer records were paired than the preregistered minimum | n/a: not a number |
| non-inferior | whether the interval's low end stayed above minus the margin | a decision about real data |
| kill flag | whether the model's field F1 fell below the preregistered kill threshold | a decision about real data |
| margin | the preregistered non-inferiority margin | n/a: not a number |
| reference | the preregistered reference model | n/a: not a number |
| repeats | repeats per model | n/a: not a number |
| condition | an E2 condition: stats_only, central_raw, central_allowed or pushdown | n/a: not a number |
| precision in the top k | the share of true candidates among a condition's top k | precision on real data |
| pushdown over central raw | pushdown's average precision over central raw reading's | a product number |
| pushdown raw text bytes | narrative bytes found in what pushdown sent across boundaries | leaks through timing, sizes or counts |
| bar verdict | whether the run met the preregistered bar (shown only when the harness gives it) | a decision about real data |
| withheld because | why the harness withheld the bar verdict | n/a: not a number |
| central | E2's central comparator: self, or a hosted model key | n/a: not a number |
| protocol minimum | the protocol's minimum candidates | n/a: not a number |
| decoy | candidates whose key is a planted decoy | false alarms on real data |
| background | candidates that are neither pattern nor decoy | false alarms on real data |
| projected minutes | E2's projected minutes: rehearsal call counts times warm-up latencies | a measurement |
| unit minutes | the unit's minutes | n/a: not a number |
| share the projection may use | the share of the unit's minutes the projection may fill | n/a: not a number |
| seeds | seeds of the run | n/a: not a number |
| why no alerts | why an openFDA channel raised no alerts | n/a: not a number |
| source | where E1's labels came from: generator or fixtures | human labels |
| paired records | records both the model and the reference scored | n/a: not a number |
| underpowered below | the paired-record count below which the comparison is underpowered | n/a: not a number |
| kill flag below | the field F1 below which the kill flag is raised | n/a: not a number |
| exceeds | whether the projection was above its share of the minutes | n/a: not a number |
| eligible | whether X1's preregistered conditions for a verdict held | a pass |
| blind | whether the plant was declared blind | that it was |
| extractor | the extractor the harness used | n/a: not a number |
| recalls in scope | public recalls the replay could score | every recall |
| recall rate | the share of those recalls a channel alerted on before the recall | a confidentiality result |
| median lead days | the median days from a channel's first alert to the recall | a promise of warning time |
| post-recall alerts | alerts that came after the recall | n/a: not a number |
| false alarms per week | alerts per week that matched no recall, on the request's product codes only | false alarms on partner data, or on the manufacturer's other product codes |
| dataset | the openFDA dataset: event or recall | n/a: not a number |
| code | an openFDA product code | n/a: not a number |
| total | records openFDA reported for the query | records fetched |
| fetched | records the lab fetched | every record |
| truncated | whether the fetch stopped at max_records_per_code | n/a: not a number |
| sheet | the labelling sheet: N1 or E1 | n/a: not a number |
| requested | rows the request asked for | n/a: not a number |
| written | rows written to the sheet | rows labelled |
| protocol records | the protocol's canary scan size | n/a: not a number |
| max calls | the request's max_calls for the hosted key | calls made |
| planned bound | the most calls the plan's units can make on the host | calls made |
| calls used | hosted calls the shard made | n/a: not a number |
| shard share | the shard's share of the hosted calls | n/a: not a number |
| tokens in | input tokens the host reported | tokens of calls without counts |
| tokens out | output tokens the host reported | tokens of calls without counts |
| calls without token counts | calls the host reported no token counts for | n/a: not a number |
| estimated USD | reported tokens times the manifest's prices | the host's bill |
| priced | whether the manifest gives the hosted model a price | n/a: not a number |
| preflight | the hosted preflight's outcome | n/a: not a number |
| preflight calls | calls the preflight made | n/a: not a number |
| Scheme | the hosted base URL's scheme | n/a: not a number |
| Host | the hosted base URL's host and port, never its path | n/a: not a number |
| model listed | whether the host listed the model id | n/a: not a number |
| model id | the host's id of the hosted model | n/a: not a number |
| visibility | which records of a planted pattern carry it: narrative_only, codes_only or both | n/a: not a number |
| label | the collective harness's own label for the row | n/a: not a number |
| recall outcomes seen before the preregistration | the requester's declaration `saw_recall_outcomes`: whether they saw recall outcomes before fixing the codes, manufacturers and settings | that the settings were frozen blind when it says no |
| warnings | the harness's own warnings for the row, as written | every problem of the run |

## Summary sections

Every section heading of the summaries (`lab.notes.HEADINGS`):

| section | what it holds |
| --- | --- |
| Lab plan | the plan: request, commit, provider, limits and purpose |
| Lab plan refused | why the plan refused the request, and the path and problem |
| Nothing to run | why a push ran nothing, and the dispatch hint |
| Models | the models the plan uses, with their pins |
| Shards | the shards: units, planned minutes and job timeouts (plan), or state, attempt and runner (report) |
| Units | the units: experiment, model, minutes and seed (plan), or status, class and wall time (shard) |
| Files to provision once per run | the server and model files the provision jobs download, with cache keys |
| Lab shard | one shard's summary |
| State | the shard's failed step, prepare problem, provenance and unit counts |
| Runner | the runner's CPU, memory, OS and Python |
| Model server and model file | the pinned server and model file a shard served, and how each was verified |
| Lab report | the report: request, plan, commit, counts and purpose |
| Units without a result | units that did not run to a result, with the reason |
| Model on runner CPU | units of display class model: measurements on this runner |
| Hosted API results: not measured on this runner | units of display class hosted-api |
| Unverified: not measurements | units of a real run whose provenance did not verify |
| Plumbing checks (fake provider): not model measurements | units a fake answered, or units of a plumbing run (a hosted unit's calls go to the configured host) |
| Units without a model | model-free units (X1, openFDA) |
| Latency and throughput cells | E3's cells |
| Canary leakage scans | G0's scans |
| Model call latency from the ledgers | per task, the successful calls' median and ninety-fifth percentile |
| Multi-site simulation: planted patterns found per channel | the simulation's channels |
| Simulation lifts, bootstrapped over patterns | the simulation's lifts |
| Simulation pushdown verification | the simulation's pushdown candidates, raw text and fallback share |
| Simulation sizing for the next request | per sim unit, the measured medians and the suggested minutes |
| Baselines blind to narrative-only patterns by construction | per sim or X1 row, S and R_mf on the plant's narrative_only patterns, with the harness's label |
| Preregistration, fixed before any model runs | E1's labels, X1's and E2's prereg hashes, E2's rehearsal |
| Extraction compared across models | E1's label, labels, reference and thresholds |
| Extraction per model, pooled over repeats | E1's per-model scores |
| Extraction paired against the reference | E1's paired comparison with the reference |
| Pushdown verification against central reading: conditions | E2's labels and conditions |
| Pushdown ratio and verdict | E2's ratio and, for a hosted central, the bar verdict |
| Pushdown candidates | E2's candidates by label |
| Pushdown sizing | E2's projection and suggested minutes |
| Evaluation harness on a plant fixture: channels | X1's label and channels |
| Evaluation harness lifts, bootstrapped over patterns | X1's lifts, eligibility and the harness's warnings |
| openFDA public replay: recalls found per channel | the requester's declaration, the replay's warnings and channels, and what the false alarms cover |
| openFDA fetches | the openFDA fetch per dataset and product code |
| Labelling sheets for a human | the N1 and E1 sheets written for a person |
| Lock | the lock status and the sentence that says what to do |
| Notes | CPU models, world digests and the notes the units carry; a sim note some sim rows lack lists the units that carry it |
| Hosted calls and estimated cost | per hosted key, calls, tokens and the estimated cost |
| Skipped by the plan | units the plan skipped (hosted units without the secrets) |
| Hosted calls: allowed and planned | per hosted key, max_calls and the planned bound |
| Hosted endpoint | a shard's hosted host, secrets' states, preflight and calls |

## Artifacts and caches

| artifact | holds |
| --- | --- |
| `lab-plan-<run id>-<attempt>` | the plan, its preregistration and the plan summary |
| `lab-prov-<run id>-<attempt>-<entry>` | one provision record (`server` or `gguf-<key>`) and its lock candidate |
| `lab-run-<run id>-<attempt>-<shard>` | one sealed shard root |
| `lab-report-<class>-<run id>-<attempt>` | `report.json`, `report.md`, `report.sources.json`, `plan.json`, `lock-candidate.json` and E1's comparison files |

The workflow's summary steps pass `--log`, so `lab.summary` also prints into the job log, between
`=== MYCELIC-LAB <label> BEGIN lines=<n> sha256=<hex> ===` and `=== MYCELIC-LAB <label> END ===` lines inside a
`::stop-commands::` window, the files labelled `plan/summary.md`; `shard/provenance.json` and `shard/summary.md`; and
`report/report.json`, `report/report.md` and `report/lock-candidate.json`, then an `INDEX` line; a missing file prints
`ABSENT`, one that cannot be read or printed line for line `UNREADABLE`, and one over 2,000,000 bytes `TOO-LARGE`.

Artifacts are kept for the request's `retention_days`. Caches (`actions/cache`): `lab-server-<tag>-<sha16>` holds the
verified server archive and `lab-gguf-<key>-<sha16>` a verified model file; a run restores only caches of its own
branch or the default branch, and an entry unused for 7 days is removed.

# Founder runbook: week-1 measurements, E1, G0, X1, the public replay, E2, follow-up and the demo

This runbook covers what you run on your own machines (STRATEGY sections 11.2 and 12):

- **E3**: latency and throughput of a model served inside a boundary;
- the **openFDA fetch**;
- **N1**: how often a device-event narrative carries information the coded fields lack;
- **freezing a domain pack** before any labelling;
- **E1**: whether a model inside the boundary extracts claims well enough, against a frontier reference;
- **G0**: whether planted text leaves a site through the Boundary (text only, synthetic data);
- **X1/X2**: the blind planted-pattern test of the frozen detectors against S, R (model-free), U and single-site
  baselines (synthetic, internal only; section 11);
- **the openFDA public replay** (STRATEGY section 9.3): signals frozen before any recall is opened (section 12);
- **E2**: whether pushdown verification with a small model inside each site keeps the ranking quality of a central
  model reading the raw text, with no raw text leaving a site (synthetic, internal only; section 13). It gates the
  architecture (STRATEGY section 5.5);
- **follow-up** (G7): the approvers file, the kill switch, checking the follow-up ledger's hash chain and the E5
  injection smoke (section 14). **Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up
  is unvalidated; nothing here measures it.**
- **part 2, the collective demo** (G8): one fictional multi-site device maker end to end, recorded, replayed,
  exported or driven live from a console (sections 15 to 17). **Fictional company, synthetic data, an illustration;
  internal use only (STRATEGY section 9.1 puts synthetic-fixture results off the YC demo's screen; showing it to YC
  is the founder's decision); never a measurement.**

None of these produced a number in the sandbox where the code was written. Model weights and api.fda.gov could not
be reached there, so every figure has to come from your runs. The E1 harness was rehearsed against local fake
servers only, and such a rehearsal writes `"measurement": false`.

## 0. Honesty rules (read these first)

1. **Never quote a number you did not measure.** If a run file does not contain it, it does not exist. Estimates
   in STRATEGY stay labelled as estimates until E3 replaces them.
2. **Label every result** with the endpoint, the host and the hardware. E3 writes the client host into `e3.json`;
   describe the server machine (model file, quantisation, GPU/CPU, server flags) with `--server-note`.
3. **A rehearsal is not a result.** A run against the fake server or a fake provider writes `"measurement": false`.
   Never quote it. Only runs with `"measurement": true` count.
4. **Say what the data is.** openFDA results are public data (`data_label: public`). MAUDE holds reportable events,
   not internal complaints. Synthetic text is labelled synthetic.
5. **Send back run files, never keys** (section 10).

Conventions:

- `python` means Python 3.11 or later.
- Run every command from the repository root.
- Nothing here needs a package beyond the standard library.
- Every command is on one line. Words in `<angle brackets>` are placeholders for your values.
- Every command accepts `--dry-run`, which checks the inputs and lists what is missing. It writes nothing and
  opens no connection.

## 1. Before you start

- Close other heavy programs while E3 runs, since it measures the machine as it is.
- Note the machine: model, CPU or GPU, RAM, power mode (plugged in or on battery).
- Keep a short log of every run id you use and what it was for.

## 2. Serve a model inside the boundary

Pick one server per machine. The runtime talks to all three through the same OpenAI-compatible API. It never adds or
removes `/v1`, so `base_url` in your routing file must be exactly the prefix your server serves. In each case below,
check the flags against that server's own `--help` or documentation for the version you install.

**llama-server** (from llama.cpp) serves one model file:

```
llama-server -m <model-file> --host 127.0.0.1 --port 8080 --parallel 4
```

- `base_url`: `http://127.0.0.1:8080/v1`.
- The `model` field in routing can be any string; the server answers with the file it loaded.
- `--parallel` sets the number of concurrent slots. E3 runs above that number measure queueing (see section 4).

**Ollama**:

```
ollama pull <model-tag>
```

```
ollama serve
```

- `base_url`: `http://127.0.0.1:11434/v1`.
- The `model` field is the tag you pulled.

**vLLM** (GPU):

```
vllm serve <model-tag> --host 127.0.0.1 --port 8000
```

- `base_url`: `http://127.0.0.1:8000/v1`.
- The `model` field is the same id you served.

**JSON output.** Start with `"response_format": "json_schema"` (schema-constrained output). If the server answers
400 to it:

- set `"response_format": "json_object"`; or
- keep `json_schema` and set `"transport_schema": "reduced"`. This sends the schema without `pattern`, length and
  range keywords. Replies are still checked locally against the full schema.

Confirm what your server version supports in its documentation, and write down which setting you used.

**Thinking (turn it off for every site task and every experiment, unless thinking is what you test).** Hybrid-thinking
models (several of the E1 example tags, `examples/e1_models.example.md`) think by default on current servers: Ollama turns
thinking on for any model that can, and vLLM and llama-server do when the model's chat template does. The thinking
tokens count against the request's `max_tokens`, and the JSON format applies only after the thinking ends, while the
site tasks give small budgets (`judge_record` 256 tokens, E2's central judge 64, E3's workloads 200 and 50). A model
that thinks past the budget returns an empty reply with `finish_reason` `length`: the runtime records `json_invalid`,
repairs with the same budget, fails again and escalates; E1 scores it as a model failure, a site judge answers
`unknown` (degraded) and an E3 cell comes back empty. Turn it off in the routing file, per endpoint (the runtime sends
nothing unless the file names it, and the file's sha256 is in every prereg and run file):

- Ollama: `"reasoning_effort": "none"` (Ollama's OpenAI-compatible API maps it to think off);
- vLLM: `"chat_template_kwargs": {"enable_thinking": false}` (the model's template reads it), or
  `"reasoning_effort": "none"` where your vLLM version accepts it;
- llama-server: `"chat_template_kwargs": {"enable_thinking": false}` with `--jinja` and a template that reads it.

`reasoning_effort` takes `none`, `minimal`, `low`, `medium` or `high`; `chat_template_kwargs` takes at most eight
names to booleans, ints or short strings. Check what your server version accepts (an unknown value can be a 400 or
be ignored) and confirm it worked: in a ledger, `finish_reason` should be `stop` and `tokens_out` well below
`max_tokens`; in E3, `thinking_requests` and `length_cut` should be 0 (section 4). The example routing file turns
thinking off for its Ollama and vLLM endpoints.

## 3. One routing file per site boundary

Copy the example and edit it:

```
cp docs/collective/examples/routing.example.json <routing-file>
```

- Set every `boundary` to the site the machine stands in, e.g. `site:<site-id>`.
- Point `base_url` at your server and set `model`.
- Delete the endpoints you do not run.
- `docs/collective/examples/README.md` explains each field and how to add a price from a provider's pricing page.
- A key for a hosted comparator goes in the environment variable named by `api_key_env`, never in the file.
- Since G6 a site's file also routes `judge_record`, the pushdown judge: it reads one of the site's own records to
  answer one question from HQ, so its endpoint (and any `escalate_to`) must be at `site:<site-id>`, the same
  boundary as `extract_claims` (`any-simulated` when one machine plays every simulated site, as in G0 and E2).
  E2 refuses a judge route that names any other boundary.

Check the file with a dry run (section 4). It reports any bad field by its JSON path, e.g.
`$.endpoints.site-a-ollama.base_url`.

## 4. E3: latency and throughput

What it does:

- sends synthetic filler text (never a record) to each endpoint you name;
- uses two workloads: `extraction` (about 1,500 words in, up to 200 tokens out) and `short` (about 750 words in, up
  to 50 tokens out);
- runs at each concurrency level, streaming every request;
- reports, per endpoint, workload and level: time to first token, end-to-end latency, decode tokens/s, the
  server-reported prompt length, throughput and, if asked, energy.

Dry run first, then the real run:

```
python -m mycelic.collective.experiments.e3_latency --routing <routing-file> --endpoint <endpoint-name> --run-id <run-id> --boundary site:<site-id> --server-note "<server-note>" --dry-run
```

```
python -m mycelic.collective.experiments.e3_latency --routing <routing-file> --endpoint <endpoint-name> --run-id <run-id> --boundary site:<site-id> --server-note "<server-note>"
```

Options:

- `--concurrency 1,4` and `--requests 20 --warmup 2` are the defaults. Warm-up requests finish before measurement
  starts and are excluded.
- Repeat `--endpoint` to compare several servers in one run.
- To compare against a hosted model **from the same network**, name its endpoint too. E3 sends only synthetic
  filler, so its runtime may call an endpoint in any boundary; keep `--boundary` set to the site the machine belongs
  to. The ledger marks such calls `external_raw_exempt`.
- On Linux with Intel or AMD RAPL, add energy (the counter is often readable only by root; if so, skip energy rather
  than running the harness as root):

```
python -m mycelic.collective.experiments.e3_latency --routing <routing-file> --endpoint <endpoint-name> --run-id <run-id> --boundary site:<site-id> --server-note "<server-note>" --energy rapl --powercap-path <powercap-path>
```

- The RAPL figure covers the whole CPU package of the client machine, including idle draw and other processes. Report
  it as that and nothing more.

Reading the result (`runs/e3/<run-id>/e3.json`):

- Check `"measurement": true` first.
- Time to first token includes time queued at the server when concurrency exceeds the server's slots (`--parallel`).
- Compare `prompt_tokens_p50` across servers for the same workload. A server whose context window is smaller than
  the prompt may cut the prompt silently, and its timings are then not comparable. If so, raise its context length
  (`-c` for llama-server; the context-length setting for Ollama) and run again under a new run id.
- `decode_tok_s` is null when the server reports no usage or streams in one chunk; that is not a failure.
- Check `thinking_requests` and `length_cut` in every cell: both should be 0. Time to first token is the first
  streamed token of either kind, answer or thinking (`reasoning_content` from llama-server, `reasoning` from Ollama
  and vLLM), and `decode_tok_s` counts thinking tokens too, so a cell where the model thought timed its thinking; a
  request cut at the token cap (`length_cut`) is a `json_invalid` failure and is in no latency statistic, so the
  slowest generations are missing. Turn thinking off (section 2) and run again under a new run id. Up to audit round
  2 the client ignored Ollama's and vLLM's `reasoning` field: TTFT waited for the end of the thinking and
  `decode_tok_s` came out about 20 times too high, with `measurement` still true.
- Failures are counted per kind in each cell (`timeout`, `http_4xx`, ...). The run still exits 0.
- `ttft_s` and `e2e_s` time each request's final HTTP try only. If `retried` is not 0 in a cell, some requests
  waited for a retry (after a 429 or 5xx answer, or a dropped connection), and the latencies understate what a
  caller waited. Report such a cell with its `retried` count, or run it again.

Suggested matrix:

- the CPU box, the M-series machine and the consumer GPU, each serving the same model file where possible;
- a hosted API from the same network as a reference;
- concurrency 1 and 4.

## 5. openFDA fetch

- Before the first run, read the current openFDA documentation (open.fda.gov) for the device endpoints, the rate
  limits and the API-key policy. The sandbox could not open those pages, so the limits below come from the openFDA
  server source: at most 1,000 records per request and a skip of at most 25,000. Confirm them.
- A key is optional but raises the rate limit. Put it in an environment variable, for example
  `export OPENFDA_API_KEY=...` in your shell (not in a file in the repository).
- The connector adds the key on the wire only. It never writes it to a file, a message or a log.

Choose 3 to 5 device product codes that have many reports in your date window. Then fetch:

```
python -m mycelic.collective.connectors.openfda fetch --dataset event --product-codes <product-codes> --date-from <date-from> --date-to <date-to> --out <cache-dir> --api-key-env OPENFDA_API_KEY
```

The command prints, per code, how many records it fetched out of the total. Check these:

- `truncated (api_max_skip)` means openFDA stops paging at its skip limit. Narrow the date window and fetch again
  into a new `--out` directory.
- `<cache-dir>/manifest.json` records every page's sha256, the coverage of lot, model, coded-problem and narrative
  fields, and `data_label: public`. A cache without a manifest is incomplete, and N1 refuses it.

## 6. N1: narrative gain over the coded fields

**Sample** 300 events, stratified by product code:

```
python -m mycelic.collective.experiments.n1_narratives sample --cache <cache-dir> --product-codes <product-codes> --run-id <run-id> --n 300 --seed <seed>
```

This writes `runs/n1/<run-id>/sheet.csv` (open it in a spreadsheet), `sheet.jsonl` and `sample.json`. If a code has
too few eligible events, its shortfall is recorded; the other codes are not topped up.

**Label** each row by reading `narrative` against the coded columns of the same row (`generic_names`,
`model_numbers`, `lot_numbers`, `product_problems`). Put `true` or `false` in each of the four columns. `yes`/`no`,
`y`/`n` and `1`/`0` are also accepted. Every cell must be filled.

| Column | `true` when the narrative names... |
|---|---|
| `component_not_in_codes` | a specific component or part (a latch, a seal, a battery contact) that no coded field names |
| `failure_mode_not_in_codes` | how it failed (cracked, leaked, overheated, came loose) beyond what `product_problems` says |
| `lot_not_in_codes` | a lot, batch or serial identifier that `lot_numbers` does not contain |
| `use_condition_not_in_codes` | a condition of use (setting, cleaning, accessory, environment, duration) that no coded field carries |

Rules for labelling:

- Label from the text alone; do not look anything up.
- When unsure, choose `false` and say why in `labeller_note`.
- Do not sort or delete rows. Do not change `mdr_report_key`.
- Save as CSV. A spreadsheet's UTF-8 byte-order mark is fine.
- Record who labelled and how long it took.

**Score**:

```
python -m mycelic.collective.experiments.n1_narratives score --sheet <labelled-sheet> --sample <sample-file> --out <out-file>
```

- `<sample-file>` is `runs/n1/<run-id>/sample.json`.
- Use `runs/n1/<run-id>/narrative_gain.json` as `<out-file>`.
- The score reports the share of narratives with any `true` label, its Wilson 95% interval, and a verdict against
  the bars fixed before the run: at least 20% supports the extraction channel; below 10%, expect the codes-only
  channel to match it; anything between is ambiguous.
- It refuses a sheet with unlabelled rows, or with keys that differ from the sample, and lists the rows to fix.

## 7. Freeze or extend a pack

E1 and every later experiment pin a pack's `vocabulary_hash`, so the vocabulary is fixed **before** anyone labels
or looks at an outcome. The built-in `device_quality` pack is an illustrative subset with invented placeholder codes
(`docs/collective/PACKS.md`); extend a copy of it before E1.

1. Copy the pack to a directory of your own:

```
cp -r mycelic/collective/packs/data/device_quality <pack-dir>
```

2. Extend the vocabulary **before labelling**: add predicates and code mappings from FDA's published device-problem
   and component code lists (the current versions, read on the day), and set each entity type's id format to the
   id shapes in your data (the `alnum` segment covers mixed letter-and-digit lot and model numbers). Add aliases for
   trade names and component phrases. Keep `pack.json`'s `illustrative` flag and disclaimer honest about what the
   copy is.
3. Bump `version` in `pack.json`.
4. Check it until it loads:

```
python -m mycelic.collective.packs.loader check <pack-dir>
```

5. Record the four hashes it prints. `vocabulary_hash` is what E1 pins; `fixtures_hash` changes when you add your
   own labelled fixtures.

The built-in pack checks the same way:

```
python -m mycelic.collective.packs.loader check <pack>
```

## 8. E1: extraction inside the boundary vs a frontier reference

What E1 decides (STRATEGY section 11.2): whether in-boundary models are within a pre-registered **5-point
non-inferiority margin of a frontier reference on field-level F1**, with at least 600 paired records, and whether
any falls below the **0.80 kill bar**. Lot and supplier exact match after canonicalisation are reported separately.
In the commands below, `<pack>` is `device_quality` or, once you have extended it, the path of your frozen copy.

**Step 1: sample records and write the labelling sheet.** From the openFDA cache of section 5 (public data):

```
python -m mycelic.collective.experiments.e1_extract prepare --pack <pack> --source openfda --cache <cache-dir> --n 600 --seed <seed> --run-id <run-id>
```

From a partner's export (JSON lines in the shape `mapping.json` describes; this stays on the partner's machine):

```
python -m mycelic.collective.experiments.e1_extract prepare --pack <pack> --source jsonl --input <partner-file> --site <site-id> --n 600 --seed <seed> --run-id <run-id>
```

This writes `runs/e1/<run-id>/records.jsonl`, `sheet.csv` and `prepare.json` (a shortfall below 600 is recorded,
not topped up).

**Step 2: label.** Open `sheet.csv` and fill `gold_claims` for every row from the narrative alone:

| Write | Meaning |
|---|---|
| `lot:L12345@crack` | the narrative says lot L12345 cracked |
| `component:battery door@!leak` | it says the battery door did **not** leak (`!` marks a negated predicate) |
| `@overheat` | it states a predicate without naming an entity: it attaches to the record's primary structured entity |
| `lot:L12345` | it names lot L12345 with no predicate |
| `-` | it states no claim at all |

Separate several items with `;`. Copy entity text as written; it must canonicalise to an id of the type. Leave no
cell empty (an empty cell is "unlabelled"). Do not sort or delete rows. Note doubts in `labeller_note`. Save as CSV;
a spreadsheet's byte-order mark is fine.

**Step 3: check the labels.** Every problem is listed by row; fix them and run again:

```
python -m mycelic.collective.experiments.e1_extract label-check --pack <pack> --records <records-file> --sheet <labelled-sheet> --out <labels-file>
```

`<records-file>` is `runs/e1/<run-id>/records.jsonl` from step 1. The command prints the sha256 of the labels.

**Step 4: pre-register.** Put every candidate and the reference in one routing file (section 3;
`docs/collective/examples/e1_models.example.md` lists STRATEGY's candidates as example tags). Then pin everything
before any model sees a record. For public data, with a hosted frontier reference:

```
python -m mycelic.collective.experiments.e1_extract prereg --pack <pack> --labels <labels-file> --routing <routing-file> --endpoint <endpoint-name> --endpoint <reference-endpoint> --reference <reference-endpoint> --margin 5 --runs 3 --seed <seed> --data-label public --boundary site:<site-id> --allow-external-raw public --run-id <run-id>
```

- Repeat `--endpoint` once per candidate. `--margin 5` means 5 points and is stored as 0.05.
- `--data-label public` is for records prepared from the openFDA cache (their site is `public`); `prereg` refuses
  it for any other records, which are partner data.
- `--allow-external-raw` lets raw records reach an `external` endpoint, and only for public or synthetic data. For
  **partner data** use `--data-label partner`, no `--allow-external-raw`, and a reference inside the boundary (a
  larger model at the site): the harness refuses to send partner records to an external endpoint, before any
  request.
- `prereg` refuses uncommitted collective code unless you pass `--allow-dirty`, which is stamped into every result.
  Commit first.

**Step 5: run each endpoint three times.** One command per endpoint and repeat (`--repeat 1`, `2`, `3`):

```
python -m mycelic.collective.experiments.e1_extract run --prereg <prereg-file> --labels <labels-file> --routing <routing-file> --endpoint <endpoint-name> --repeat 1 --run-id <run-id>
```

`<prereg-file>` is `runs/e1/<run-id>/prereg.json` from step 4. `run` refuses, before writing anything, if the labels,
the pack's vocabulary, the scoring code or any pinned endpoint setting (model, provider, boundary, response format,
transport schema, thinking controls) changed since `prereg`. Ctrl-C leaves `run.json` with `complete: false`; start a new run id.
When a run ends, `run.json` records the sha256 of its `predictions.jsonl` and `ledger.jsonl`; do not edit either
file, or `compare` refuses the run.

**Step 6: compare.** List every run directory of every endpoint. `compare` refuses a pre-registered endpoint
without runs; `--allow-incomplete` accepts it (and incomplete runs), stamps that, and lists the endpoint under
`endpoints_without_runs` in `e1.json`:

```
python -m mycelic.collective.experiments.e1_extract compare --prereg <prereg-file> --run-dirs <run-dirs> --run-id <run-id>
```

**Reading `runs/e1/<run-id>/e1.json`:**

- Check `"measurement": true` first. A run against a fake server says false; then `non_inferior` and `kill_flag`
  are null and `verdicts_withheld` says why. Never quote such a file.
- `endpoints.<name>.field_f1` is the primary metric, with a bootstrap 95% interval over records. `claim_f1`,
  `entity_f1` and `predicate_f1` are secondary and never replace it.
- `paired.<name>` compares each candidate with the reference record by record: `mean_diff`, `ci95` and an exact
  sign test. `non_inferior` is true only when `ci95` low is above minus the margin. `underpowered` is true below
  600 paired records; report such a result as underpowered. `kill_flag` is true below 0.80 absolute field F1.
- `exact_match.lot` and `exact_match.supplier` report lot and supplier exact match separately: `n` counts records
  whose labels name a lot (or supplier), `matches` those whose predicted ids equal the labelled ones in every run,
  with a Wilson interval over records. Each `run.json` has that run's own rate.
- `models_served` and `model_mismatch` show whether a server answered with a different model than you asked for.
- **Transport failures are the server's, not the model's.** A record whose extraction ended in `timeout`,
  `network`, `http_4xx`, `http_5xx`, `too_large` or `no_handler` is not scored (`scored: false` in
  `predictions.jsonl`): it is left out of every F1, exact-match figure and paired comparison, and the JSON validity
  rates count only attempts that reached the model and came back. `failures` (per endpoint in `e1.json`, per run in
  `run.json`) counts the transport and the model failures by kind, and `transport_failure_share` is the transport
  share of the endpoint's record runs. When either side of a paired comparison has transport failures on more than
  1% of its record runs, `paired.<name>.withheld_reason` says so and `non_inferior` and `kill_flag` are null: fix the
  server (timeouts, memory, the context size) and re-run that endpoint's repeats. A fake run says
  `measurement_false` there.

## 9. G0: does planted text leave a site? (text only)

G0 plants unique canaries in a synthetic world's records (letter-only tokens, id-shaped person data and id-shaped
tokens that appear only in narratives), runs every site through ingest, extraction and emission, and scans every byte
that crossed a site boundary for the canaries and for 24-character narrative fragments. `docs/collective/LEAKAGE.md`
explains what it checks and, as important, what it does not: it proves only that text did not leave. Counts and
claims can still reveal things (X5); never quote a G0 result as "no leakage".

Everything is synthetic and needs no network, no key and no model server. `<pack>` is `device_quality`,
`claims_integrity` or the path of your frozen pack; `--out` must not exist yet (or be empty).

The default run (fake in-process model, the pack's own `require_master_data`):

```
python -m mycelic.collective.experiments.g0_canary --pack <pack> --records 1000 --seed <seed> --out runs/g0/<run-id>
```

The same with master data off, to see what crosses when ids found only in narratives may leave as cell keys (the
report lists them under `known_limitation`; the run writes a copy of the pack with its own `config_hash`):

```
python -m mycelic.collective.experiments.g0_canary --pack <pack> --records 1000 --seed <seed> --require-master-data off --out runs/g0/<run-id>
```

Through a real model server instead of the fake (section 2). The routing file must route both `extract_claims` and
`judge_record` (G6), and every endpoint they name must declare `"boundary": "any-simulated"`, because one machine
plays every simulated site; a file without a `judge_record` route exits 2. The run is still synthetic and measures
nothing about the model; it only checks that the path through a real server leaks no text:

```
python -m mycelic.collective.experiments.g0_canary --pack <pack> --records 1000 --seed <seed> --mode routing --routing <routing-file> --out runs/g0/<run-id>
```

Reading `runs/g0/<run-id>/leakage.json`:

- The command prints one line, `g0: pack=... canaries=... hits=... shingle_overlap_bytes=... known_limitation=...
  -> PASS|FAIL`, and exits 0 on PASS, 1 when something leaked (or the scanner's positive control found nothing) and
  2 on a usage or configuration error.
- `hits` and `shingle_overlap_bytes` must be empty and 0. Each hit names the file and the canary id, never the token.
- `positive_control` must show canary hits and narrative bytes in the first site's own database; otherwise the
  scanner is broken and the run fails.
- `scope` is `text-only` and `not_covered` lists what G0 does not test.
- `edge_totals.cells_n_ge_k` shows how many weekly cells had a count of at least k. At the built-in synthetic
  volumes it is 0: every weekly cell is suppressed (`LEAKAGE.md` section 1).
- Since G6, `stages` is `["edge", "pushdown"]`: HQ detects over the cells, asks the sites about up to 20 candidates
  (at least 5), and every question, every verdict, each site's ingress log and HQ's own database are scanned too.
  `pushdown_totals` counts the candidates, questions, routes, verdicts and conclusions (`LEAKAGE.md` section 9).
- Since G7, `stages` is `["edge", "pushdown", "followup"]`: every supported conclusion gets its follow-ups proposed,
  approved by a **simulated** owner (`followup_totals.simulated_approvals` is `true`) and executed, and every packet,
  packet request, draft, the follow-up ledger, the outbox and the central draft ledger are scanned too.
  `followup_totals` counts them and carries the ledger's head hash; the layer is built ahead of E2 and X4 and
  unvalidated (`LEAKAGE.md` section 10).

Send back `leakage.json` only. **Never send `private/manifest.json`**: it is the one file that holds the canary
tokens, and a scan of anything that contains it is refused.

## 10. What to send back

Send these files:

- `runs/e3/<run-id>/e3.json` and `runs/e3/<run-id>/ledger.jsonl`, with your note on the server setup;
- `<cache-dir>/manifest.json` (the pages only if asked; they are public data but large);
- `runs/n1/<run-id>/sample.json`, the labelled sheet and `runs/n1/<run-id>/narrative_gain.json`;
- the four hashes of your frozen pack (section 7);
- for E1: `prereg.json`, every `run.json` and `e1.json`;
- for G0: `runs/g0/<run-id>/leakage.json` (never `private/manifest.json`);
- for X1: `runs/x1/<run-id>/prereg.json`, the planter's plant spec, and the run's `labels.json` and
  `scorecard.json` (never the `work/` directory: it holds the synthetic world's site stores, which are large and
  add nothing);
- for the replay: the three run files `prereg.json`, `signals.json` with `phase1.json`, and `replay.json`, plus both
  caches' `manifest.json` (the pages only if asked; they are public data);
- for E2: the X1 `prereg.json` it used, the plant spec, `runs/e2/<run-id>/e2.json` and `central.ledger.jsonl` (never
  the `work/` directory: it holds every simulated site's store, ledgers and logs);
- for the E5 injection smoke: `runs/e5/<run-id>/e5.json` only (never the variant directories: they hold the synthetic
  world's site stores and full packets).

Never send:

- an API key, a `.env` file, or a shell history that contains a key;
- partner data of any kind: for E1 on partner data, never send `records.jsonl`, the sheets, `labels.jsonl` or
  `predictions.jsonl`; `prereg.json`, `run.json` and `e1.json` hold no record text and can be sent.

The ledger holds numbers and labels only (no prompt, no reply, no record text), so it is safe to send. One label
comes from the server: `model_served` is the model id each server reported, kept only when it is a short plain
identifier. Look over that column before you send the ledger if your servers use names you would rather not share.

## 11. X1: the blind planted-pattern test (synthetic, internal only)

X1 (STRATEGY section 11.2) asks whether the cross-site detectors find planted patterns that a single site alone would
miss, at the same weekly alert budget, without anyone tuning them to the plant. X2 reports the same run against the
baselines: S (codes only), R (model-free) over the fields allowed to leave, U (an unrestricted reference) and each
site alone. Everything here is a **synthetic, same-author world**: the scorecard says `synthetic: true`,
`internal_only: true` and `measurement: false`, and it is never shown to a buyer (STRATEGY section 9.1).

**Roles.** The **detector author** (whoever wrote `mycelic/collective/detect/`) runs `prereg` and `run`. The
**planter** is someone else who writes the plant spec without looking at the detectors.

- The planter may read: the run's `prereg.json`, `docs/collective/ARCHITECTURE.md` section 14.2 (the plant spec
  format) and the pack's `vocabulary.json`, `aliases.json` and `generator.json` (which ids, predicates, templates
  and sites exist).
- The planter may not read: `mycelic/collective/detect/`, `ARCHITECTURE.md` section 13 or the pack's
  `detectors.json`. If they did, they set `planter_saw_detector_code` to true, and the run is not blind.
- The spec needs at least 20 patterns and 20 decoys (each a JSON object; `fixtures/plant_smoke.json` in each pack
  shows the shape, though it is a construction smoke, not blind). Spread them over the evaluation weeks; for 40 items
  use `--weeks 104`.
- The planter copies the `prereg_sha256` that `check-plant` prints into the spec, so the spec is bound to this
  prereg and to nothing else.

**Step 1 (detector author): freeze the settings, the pack and the code** before any plant spec exists. Commit
first; uncommitted evaluation code is refused unless you pass `--allow-dirty` (stamped):

```
python -m mycelic.collective.evaluate.harness prereg --pack <pack> --seeds <seeds> --weeks 104 --eval-from 26 --eval-to 103 --tie-salt <tie-salt> --detector-author "<detector-author>" --run-id <run-id>
```

`--eval-from` must leave room for the detectors' window and history (the command says how much); weeks before it
are burn-in. Send `runs/x1/<run-id>/prereg.json` to the planter.

**Step 2 (planter): check the spec** against the prereg until it prints `plant: ok`. It also prints the prereg's
sha256 for the spec's `prereg_sha256`:

```
python -m mycelic.collective.evaluate.harness check-plant --prereg <prereg-file> --plant <plant-file>
```

Errors name a JSON path and a fixed problem, for example `plant: $.decoys[3].sites: too few sites`.

**Step 3 (detector author): run every seed.** `run` refuses (exit 2, no scorecard) a changed pack or code hash
(listing every changed name), other seeds, a spec bound to another prereg, an existing run id and uncommitted code:

```
python -m mycelic.collective.evaluate.harness run --prereg <prereg-file> --plant <plant-file> --seeds <seeds> --run-id <run-id>
```

It prints one line ending in `(synthetic, internal only, not a measurement)` and writes
`runs/x1/<run-id>/labels.json` and `scorecard.json`. `--ablation-k1` adds an internal ablation without
suppression. Ctrl-C exits 130 and leaves a partial run directory; start again under a new run id.

**Reading `scorecard.json`:**

- `x1.eligible` is true only when the run is blind (self-declared), the spec is bound to the prereg, and there are
  at least 20 patterns and 20 decoys; `x1.reasons` lists every failing condition. Only then is `x1.verdict` filled:
  `lift_ci_low_above_0` (the 95% cluster-bootstrap interval of `lifts.X_minus_single_site` excludes 0),
  `precision_at_40_at_least_0_25` and `pass`.
- `channels.<name>` gives recall (found / patterns x seeds), recall by visibility, median delay and lead in weeks,
  tie-averaged precision@40 and AP, false alarms per week and how many decoys of each class alerted. Read X against
  S, R_mf, single_site and U; `rules` is unranked.
- **Every seed also runs without the plant** (the control, `work/seed-<seed>/control/`). A channel's find is a
  chance find when the control finds the same pattern in the same week or earlier: its key alerts that early on the
  background alone. A control alert that comes only later is not one; the plant's alert came first, and in the
  planted world its cooldown hides the later background alert. Each channel reports `control_found` (the control's
  finds, any week of the window), `control_recall`, `control_alerts` and `chance_found` next to `found`, and
  `found_net` / `recall_net` count the finds that are not chance finds; each pattern outcome says
  `found_in_control` and `chance_find`, and `control_alerts` lists the control's alerts. The lifts compare net found
  (`lifts.*.basis`). Read `recall_net`, not `recall`, when you compare channels: single_site in particular can
  "find" a pattern on a busy site's background. single_site counts a find only at one of the pattern's planted
  sites.
- `min_detectable_rate` is arithmetic on the pack's settings: the smallest constant weekly rate each per-site test
  can certainly see over a constant background, at k, unsuppressed, and for X with the background in text-only
  cells (`rate_at_k_text_background`).
- `by_construction` says what S and R_mf cannot see by construction (narrative-only plants); it is **not a result**.
- `known_hard_cases` lists unmarked cross-site copies, which count as independent at each site.
- `warnings` include decoys that were not quiet elsewhere (background noise on their key) and fewer than 10
  patterns.
- `content_hash` is reproducible: the same prereg and spec give the same hash on any machine and run id.

## 12. The openFDA public replay (STRATEGY section 9.3)

**Label every use of it: "public data, artificial partitioning, not a confidentiality demonstration."** The
replay works **within one manufacturer only**, never across manufacturers. The partition into "sites" is artificial
(an event field, such as the event location) and says nothing about confidentiality. The recall fields the scorer
uses (`event_date_initiated`, `product_code`, `recalling_firm`, `root_cause_description`) are named in FDA's own
schema, but their **contents and coverage are unverified** (the sandbox could not reach api.fda.gov); check
`signals.json`'s coverage block before you trust a run. The built-in `device_quality` pack is an illustrative subset
whose id formats will not resolve most real model or lot numbers: **freeze a pack copy with this manufacturer's id
shapes first** (section 7), or `signals` warns `low_resolution`.

**Step 1: fetch the events** for the manufacturer's product codes (section 5):

```
python -m mycelic.collective.connectors.openfda fetch --dataset event --product-codes <product-codes> --date-from <date-from> --date-to <date-to> --out <events-cache> --api-key-env OPENFDA_API_KEY
```

**Step 2: pre-register** before you look at any recall. Name the manufacturer exactly as the events spell it
(repeat `--manufacturer` for each spelling; nothing is merged fuzzily), the field paths of the manufacturer name and
of the partition, and declare honestly whether you have already seen this manufacturer's recalls:

```
python -m mycelic.collective.experiments.openfda_replay prereg --pack <pack-dir> --events-cache <events-cache> --manufacturer "<manufacturer>" --manufacturer-field "<manufacturer-field>" --partition-field "<partition-field>" --date-from <date-from> --date-to <date-to> --tie-salt <tie-salt> --saw-recall-outcomes no --run-id <run-id>
```

**Step 3: compute the signals** from the events alone. They are frozen in `signals.json`, whose sha256 goes into
`phase1.json`; this command has no recall argument:

```
python -m mycelic.collective.experiments.openfda_replay signals --prereg <prereg-file> --run-id <run-id>
```

**Step 4: fetch the recalls** (only now):

```
python -m mycelic.collective.connectors.openfda fetch --dataset recall --product-codes <product-codes> --date-from <date-from> --date-to <date-to> --out <recalls-cache> --api-key-env OPENFDA_API_KEY
```

**Step 5: score.** `score` refuses a `signals.json` that differs from `phase1.json` or was made under another
prereg, before it opens the recall cache:

```
python -m mycelic.collective.experiments.openfda_replay score --prereg <prereg-file> --signals <signals-dir> --recalls-cache <recalls-cache> --run-id <run-id>
```

Use a new `--run-id` for each of the three commands. Reading `runs/replay/<run-id>/replay.json`:

- `measurement` is true only when both caches hold public data (`data_label: public`).
- Per channel (X, S, R_mf): `found` counts recalls with an alert on their product code available before the
  initiation date, within `--lookback-weeks`; `median_lead_days` is how long before. Alerts after the initiation are
  counted as `post_recall_alerts` (stimulated reporting) and never as found.
- `false_alarms_per_week` counts alerts on **every** product code of the manufacturer, not only recalled ones.
- **Read `found` only against chance.** A recall is matched on its product code alone, so a channel that alerts on
  a code often "finds" its recalls at almost any date. Each channel reports `alerts`, `alerts_per_week`,
  `found_minus_expected` and `chance`: the channel's own alerts shifted in time by every whole number of weeks
  (keeping how many alerts each code has and how they cluster) against the recalls at their real dates.
  `chance.expected_found` is what the channel's alert volume finds by timing alone; `chance.p_value` is the share of
  shifts that found at least as many recalls as the real alignment (never below one over the number of evaluated
  weeks); `chance.per_recall` says how easily each recall is found by chance. A channel whose `found` does not clear
  its `expected_found` with a small `p_value` has shown nothing, however high its `found`. Compare channels by
  `found_minus_expected`, never by `found`.
- `recalls.unmatched_product_code` lists recalls of codes with no event in the cache; `not_evaluable` recalls fall
  before the first evaluated week.

## 13. E2: pushdown verification against central reading (synthetic, internal only)

E2 (STRATEGY sections 5.5 and 11.2) gates the architecture: on planted synthetic worlds, does pushdown verification
with a small model inside each site rank the detector's candidates at least 90% as well (AP) as one central model
reading the raw text of every site's matching records, with zero raw-text bytes crossing a boundary? It compares four
conditions on the same candidates:

- `stats_only`: the detector's own snapshot score, no question asked;
- `central_raw`: one central judge reads the raw record text of every site's matching records in the question window.
  Raw text crosses site boundaries, so it runs on synthetic worlds only, and you must say so on the command line;
- `central_allowed`: STRATEGY's R for this task: the same central judge reads only the fields policy allows to leave
  (site, received date, codes and structured ids), never narrative;
- `pushdown`: each contributing site and up to two sibling sites answer a narrow question from their own records
  inside their boundaries; only bucketed verdicts cross; ranked by the commit gate's status, then the support lower
  bound.

Everything here is **synthetic and same-author**: `e2.json` says `synthetic: true` and `internal_only: true`, and it
is never shown to a buyer or as a product number (STRATEGY section 9.1).

**Inputs.** An X1 prereg (section 11, step 1) with at least 5 seeds, and a plant spec checked against it (section 11,
step 2). The protocol needs at least 300 candidates over at least 5 seeds; below that the run exits 2 unless you pass
`--min-candidates N`, which `e2.json` stamps as `below_protocol_minimum`. The device pack ships
`fixtures/plant_e2_smoke.json`, a same-author smoke (not blind, never a result) that yields enough candidates on a
6-site, 52-week world with evaluation weeks 20 to 51.

**Rehearsal with fakes** (no model server; every site judges with the deterministic lexical judge behind a fake
provider, and the central conditions use rehearsal-only fake handlers). It checks the plumbing and writes
`measurement: false`; it says nothing about any model:

```
python -m mycelic.collective.evaluate.harness prereg --pack device_quality --seeds 1,2,3,4,5 --weeks 52 --eval-from 20 --eval-to 51 --tie-salt <tie-salt> --detector-author "<detector-author>" --run-id <run-id>
```

```
python -m mycelic.collective.experiments.e2_pushdown run --x1-prereg <prereg-file> --plant mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json --run-id <run-id> --allow-external-raw synthetic --data-label synthetic
```

**A real run.** Serve the small model you are testing (section 2) and write **one routing file per site boundary**
into one directory, named `<site-id>.json` for every site of the prereg's world (`world.site_ids` in
`prereg.json`). Each routes `judge_record` (and any `escalate_to`) to an endpoint whose boundary is `site:<site-id>`
or `any-simulated` (one machine playing every site), never the fake provider; E2 refuses anything else with "the judge
route of site <site-id> may leave the site". Copy `docs/collective/examples/central_routing.example.json` for the
central comparator: it routes `judge_candidate_raw` and `judge_candidate_allowed` to the model you compare against,
at a `central` (or `external`) boundary; E2 refuses a site boundary or the fake provider there.

```
python -m mycelic.collective.experiments.e2_pushdown run --x1-prereg <prereg-file> --plant <plant-file> --run-id <run-id> --site-routing <site-routing-dir> --central-routing <central-routing-file> --central-context-tokens <context-tokens> --allow-external-raw synthetic --data-label synthetic
```

`--central-context-tokens` is required with `--central-routing`: the context one request gets on the central server. For
Ollama that is the model's `num_ctx` (check what your server uses; set it with `PARAMETER num_ctx` in a Modelfile or
with the server's context-length environment variable when you start `ollama serve`: Ollama's OpenAI-compatible API
takes no request options, so neither the runtime nor the routing file can set it); for llama-server it is `-c`
divided by `--parallel`, since the slots share the context. A central_raw prompt holds up to 400
records of raw text and can run to thousands of tokens (the fake's rough estimate, a quarter of the payload's bytes, put
the rehearsal's longest near ten thousand; a real tokenizer differs), and a server that cuts a prompt silently shows the
central reference less than it was given. E2 keeps each central_raw call's server-reported prompt tokens and counts
every prompt that, with the 64 output tokens, reaches the context you declared (or did not report its tokens); any such
prompt withholds the bar. Declare the real number: a larger one than the server gives hides truncation.

`--top-n` (default 60) is the number of candidates per seed; `--deadline-seconds` (default 600) is how long HQ waits for
one site's verdict before it records a timeout, after which E2 waits up to the same time again for the late answer and
scores the item on it (a site answers one question at a time, so a late answer would otherwise also delay the next
question to that site); `--bootstrap-b` (at least 1000, default 10000) and `--bootstrap-seed` set the paired bootstrap.
`--dry-run` lists what is missing and writes nothing.

**How long it takes: estimate it from your own E3 numbers; there is no figure here.** Run the rehearsal first. In
its `e2.json`, `items[].central_raw_records` is how many records the central judge read for each candidate across all
sites (at most 400); each site's judge makes one call per record it retrieves for a question at most (capped by the
pack's `verify_max_records`; a question asked again reuses the stored verdict), so the sum of `central_raw_records`
over the items bounds the site judge calls, give or take the two caps. The central conditions add two calls per
candidate. Multiply the calls by the per-call latency E3 measured on your hardware at your concurrency (section 4) to
get an estimate. The per-entity daily question budget (`question_budget_per_entity_per_day`) turns extra questions
about one entity on one simulated day into `unknown` (budget), and so does the per-site one
(`question_entities_per_site_per_day`) for questions about more distinct entities than it allows on one day;
`pushdown.budget_unknowns` counts both.

**Reading `runs/e2/<run-id>/e2.json`:**

- `stamps.measurement` is true only when no fake took part anywhere (every site endpoint, the central endpoint and
  every ledger row). **The 0.90 bar is judged only then, and only on a complete, informative run**: `verdict` holds
  `ratio_at_least_bar`, `ci_low_at_least_bar`, `lift_ratio_at_least_bar`, `pushdown_raw_text_bytes_zero` and `pass`.
  Otherwise `verdict` is null, `verdicts_withheld` is true and `withheld_reason` says why, the first that applies of:
  a fake was involved; the central judge failed on some items (`central.failures` by kind: fix the server and
  re-run); a central_raw prompt reached the context you declared or did not report its tokens
  (`central.at_context_limit`: raise the context on the server and in `--central-context-tokens`); more than 5% of
  the pushdown routes went unanswered (`pushdown.unanswered_share`: a timeout, an error or a degraded judge; the run
  measured your deadline and servers, not pushdown); the ratio is undefined (central_raw's AP is null or below 0.01);
  or the candidate pool is **uninformative** (central_raw does not beat a random order: its lift over `chance` has a
  95% interval reaching 0).
- `conditions.<name>`: AP and precision@40 with paired percentile intervals, resampled by candidate key
  (`bootstrap.clusters`, `n_clusters`: one key in several seeds is one cluster). `ratio.estimate` is pushdown AP over
  central_raw AP with its paired interval. `chance` is the AP of a random order, close to the share of true candidates,
  and `ratio.lift_estimate` is the **chance-corrected ratio**, pushdown's AP minus chance over central_raw's AP minus
  chance: how much of central_raw's gain over a random order pushdown keeps. Read it first. When most candidates are
  true, any verifier, even one that scores every candidate the same, has an AP close to central_raw's and an AP ratio
  near 1; its chance-corrected ratio is 0. On the shipped smoke fixture 247 of 300 candidates are true, so a run on it
  is withheld as uninformative unless the judges separate far better than chance: a real E2 needs a plant spec with many
  more decoys and background keys.
- `central`: central-call failures by condition and kind, items left out of the statistics for them
  (`failed_items`, also `bootstrap.excluded_central_failures`), and the central_raw prompt tokens (`prompt_tokens_max`,
  `prompt_tokens_median`, `prompt_tokens_unreported`, `at_context_limit`). Each item has its `central_errors` and
  `central_raw_prompt_tokens`.
- `raw_text_bytes`: `central_raw` is the UTF-8 bytes of record text it sent (more than 0 by design); `stats_only` and
  `central_allowed` are 0; `pushdown` is the narrative overlap found in every artifact that crossed (questions,
  verdicts, the site ingress and egress logs) and must be 0. `raw_text_scan` shows how each was scanned.
- `pushdown.resolvability`: the share of supported conclusions whose every counted confirm resolves, at its site,
  to in-window records of that site judged yes/yes (STRATEGY section 6.3 targets at least 95%), and how many
  confirming records the site's extractor had missed (`extraction_miss_confirmations`).
- `pushdown.statuses`, `verdicts`, `routes`, `budget_unknowns`, `timeouts` and `errors` describe the pushdown run;
  `late_collected` counts answers that came after the deadline and were scored, `unanswered` and `unanswered_share`
  the routes that still ended without an answer (timeout, error, or a degraded judge: a site whose judge failed on
  more than half of the records answers `unknown` with quality `degraded`, keeps nothing and judges again when asked
  again);
  `items[]` lists every candidate with its label (`true`, `decoy` or `background`), status and four scores.
- `notes` say what the data is: synthetic, same-author, internal only; cross-site copies without an origin marker
  count as independent roots at each site, so pushdown can reach `supported` on them (a known hard case).
- `content_hash` is reproducible: the same prereg, plant spec and settings give the same hash on any machine and run
  id.

Send back what section 10 lists for E2. `e2.json` and `central.ledger.jsonl` hold no record text, record ref or
narrative (the ledger refs are `e2:<n>:raw` and `e2:<n>:allowed`), and a test scans them for narrative shingles.

## 14. Follow-up (built ahead of E2 and X4; unvalidated)

**Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.** STRATEGY section 7 says X4 (20 historical cases: time to an approved draft, edit distance, rejection
and false-action rates) must run before this layer is shown; it has not. Never show a follow-up figure as a product
number.

What the layer does (ARCHITECTURE section 16): for a `supported` conclusion, the system proposes allow-listed
follow-ups; a named owner approves, edits or rejects each one; an approved T0 assembles a read-only evidence packet
inside each target site, of which only a bucketed, suppressed summary crosses; an approved T1 appends a draft to an
outbox. Every step is one entry of the append-only, hash-chained ledger `followups.sqlite3`. Writes to a system of
record (T2) are off and have no executor.

**The approvers file.** Copy `docs/collective/examples/approvers.example.json`, set `enterprise` to your org's, and
list each person with one of the pack's roles and the unit they answer for. A person decides on a follow-up only when
they hold its owner or escalation role at a unit that covers every target site; the file is read at every decision.
`examples/README.md` explains each key.

**The kill switch.** Copy `docs/collective/examples/kill_switch.example.json`: `global` `on` stops everything, `types`
stops one follow-up type. Keep the file in place: a missing or unreadable file counts as ON. The environment variable
`MYCELIC_FOLLOWUP_KILL` (`all`, or a comma-separated list of type ids) adds to the file; any other value counts as ON.
Proposing and approving are refused (`kill_switch`) while the switch is on, and executing records `blocked` instead
of running; rejecting is not affected.

**Check a ledger's hash chain** before you trust a run's follow-up trail. Pass the head hash and entry count the run
exported (G0's `leakage.json` has them as `followup_totals.ledger_head_hash` and `ledger_entries`): without them a
truncated tail or a complete rewrite of the file passes, because the chain has no key.

```
python -m mycelic.collective.followup.ledger verify --ledger <ledger-file> --expected-head <head-hash>
```

It prints one JSON line (`ok`, `entries`, `head_hash`, `problem`, `seq`; never a payload) and exits 0 when the chain
is whole, 1 when it is broken (the problem and the seq it was found at) and 2 when the file is missing, not a ledger,
or damaged below the chain (`unreadable`: a damaged page or header, an undecodable `ledger_info` cell or schema name).
A flipped byte inside an entry is reported at its seq (`bad_entry` or `prev_hash_mismatch`), never as a traceback.
`--expected-entries N` adds the count to the anchor.

**The E5 injection smoke** plants instruction-shaped sentences that name a fresh id (`<injected-id>`, canonical for
`<entity-type>`, found nowhere in the world or the pack) in 1% of one site's record narratives, runs G0's stages
(edge, pushdown, follow-up), and checks that the id reaches no supported conclusion, proposal, draft, outbox line,
ledger entry or packet, while HQ's cells do name it (the positive control). A second variant injects at two sites and
is recorded, never asserted: a coordinated campaign is indistinguishable from real records for any extractor.

```
python -m mycelic.collective.experiments.e5_injection --pack <pack> --records 1000 --seed <seed> --entity-type <entity-type> --injected-id <injected-id> --out runs/e5/<run-id>
```

For the device pack use `--entity-type supplier` with an id such as `V9999`; for the claims pack `--entity-type
repair_shop` with `RS-99999`. It exits 0 when the single-site variant passed, 1 when it did not, and 2 (writing
nothing) when the type has no id format, the id is not canonical or already in the world, `--rate` is outside
(0, 0.2] or `--out` is not empty. **It is a plumbing smoke, not E5:** the extractors here are the lexical extractor
or a fake replaying it, so the injected text is inert by construction. E5 proper needs your in-boundary model
(section 2) and is not built here. `e5.json` says `measurement: false`, `synthetic: true` and
`simulated_approvals: true`.

# Part 2: the collective demo (G8)

## 15. The demo: record, lint, replay, export, serve

**Fictional company (Halvern Medical), synthetic data, a constructed illustration. Internal use only: never show it
to a buyer, never quote a number from it** (STRATEGY section 12), **and not the YC demo as STRATEGY is written**:
section 9.1, the YC demo's rules, puts synthetic-fixture results of any kind off the screen. Whether to show it to YC
is the founder's decision (`demo/collective/README.md`). Every run file says
`measurement: false`. `demo/collective/README.md` explains what the demo shows and what it does not;
`demo/collective/SCRIPT.md` is the talk track.

**Record** the full loop headless, with the deterministic stand-in model at every plant (no model server needed). The
directory's name becomes the run id; `--record` alone writes to `runs/collective/<random run id>`:

```
python demo/collective/collective_demo.py --record runs/collective/<run-id>
```

It prints one summary line (the hero's X rank, the gate status and how many of the twelve checks passed) and the time
it took, and writes six files: `scorecard.json`, `trace.json`, `ledger.jsonl`, `leakage.json`, `approvals.jsonl` and
`screen.json`. Exit 0 means every check passed; 1 means the files were written and a check failed (the failed checks
are on screen); 2 means nothing was written (a usage, scenario, routing, endpoint or run-file problem, printed as one
`error:` line). Ctrl-C writes nothing and exits 130. The work directory, which holds the raw synthetic narratives and
the canary manifest, is removed at exit unless you pass `--keep-workdir`; never send it.

**Lint** a run: every number on screen must read back from a primary run file, and no static text, console string or
talk-track line may hold a digit, a number word, a denylisted phrase or a benchmark figure:

```
python demo/collective/lint_numbers.py runs/collective/<run-id>
```

It prints `lint: ok (...)` and exits 0, or one `lint: <file>: <rule>: <token>` line per violation and exits 1, or one
`error:` line and exits 2 (a missing directory or file, invalid JSON, or run files of another schema version).

**Replay** a recorded run (no engine; the committed run under `demo/collective/recorded/` when no directory is given)
at `http://127.0.0.1:8766/`, and **export** one as a standalone page (under 2 MB, no network, opens from disk):

```
python demo/collective/collective_demo.py --replay <recorded-dir>
```

```
python demo/collective/collective_demo.py --export <page-file>
```

`--export` takes the committed run unless you pass `--run <recorded-dir>`. A port in use, a missing file or an older
schema version prints one `error:` line and exits 2. A replay and an export always show the RECORDED badge, also for
a run that was driven live with `--serve`: nothing runs behind them. Their footer still says how the run was made.

**Serve** the live console at `http://127.0.0.1:8765/`: the engine runs now, the presenter presses "Check with sites"
and approves each follow-up as the named owner, and the badge says LIVE. The run files are written to
`runs/collective/<run-id>` (or `--out`) when the last beat ends:

```
python demo/collective/collective_demo.py --serve
```

A control pressed at the wrong time answers 409, pressing one twice answers `done_before` and changes nothing, and a
failure (an endpoint, a fallback) shows the error and the replay command on the console, which stays up until Ctrl-C.

**The 60-second cut live:** add `--cut`. The engine then walks only the cut's beats (the problem, the alert, the
check, real data); the console shows only those and offers no full version. The follow-ups still run, with scripted
approval, when you press Next after the check, so the run files are complete; they say `approval: recorded`, as a
`--record` run does. Without `--cut` the console's "Show the 60-second cut" button only filters the view: the engine
still walks the follow-up beat, which stays on screen while it is live, and real data opens once you approve both
follow-ups (audit round 2: up to then the button hid the live beat and left an untitled page with no way on).

```
python demo/collective/collective_demo.py --serve --cut
```

## 16. Recording with a real local model

The plants can extract and judge with a model you serve (section 2) instead of the stand-in. On one machine every
plant is simulated in one process with one shared model; the screen says "sites simulated in one process, one shared
model" and names the endpoint and the model tag, and `measurement` stays false. **Never show this run to a buyer
either:** the world is still synthetic and same-author.

The routing file routes `extract_claims` and `judge_record` (and any `escalate_to`) to `openai_compat` endpoints at
boundary `any-simulated`; `draft_followup` is optional (`central` or `any-simulated`; without it HQ's template drafter
writes the CAPA draft from structured inputs only). The demo refuses the fake provider and `site:` boundaries here:
the stand-in already runs without `--routing`, and one machine plays every plant. For example (set `base_url` and
`model` to your server's):

```
{
  "schema_version": 1,
  "endpoints": {
    "local": {"provider": "openai_compat", "boundary": "any-simulated", "base_url": "http://127.0.0.1:8000/v1",
              "model": "<model-tag>", "response_format": "json_schema"}
  },
  "routes": {"extract_claims": {"endpoint": "local"}, "judge_record": {"endpoint": "local"}}
}
```

```
python demo/collective/collective_demo.py --record runs/collective/<run-id> --routing <routing-file>
```

```
python demo/collective/collective_demo.py --serve --routing <routing-file>
```

Before anything is built the demo asks every routed endpoint `GET /models`; one that does not answer stops the run
with exit 2, naming the endpoint, and prints the `--replay` command so the talk can go on from the recorded run.
**The demo never falls back silently:** if extraction at any plant falls back to the lexical extractor, or a judge
degrades (a schema-invalid reply after the repair retry, a timeout), the run stops with exit 2, names the endpoint and
the error kinds, and writes nothing.

## 17. Live vs recorded, and what to send back

- **The badge** says LIVE only while the console drives the engine now (`--serve`); `--record`, `--replay` and
  `--export` say RECORDED, whatever mode the run was made in (`screen.json`'s `presentation`). **The footer** says
  how the run was made (`mode`): a **scripted run** (`--record`: the approvals were scripted, `approval: recorded`,
  the screen says "recorded approval (scripted)", and each execution is requested twice to show it runs once) or a
  **run driven live in the console** (`--serve`: the presenter approved each follow-up, acting as the named owner,
  `approval: live`, and each execution was requested once; with `--cut` the follow-ups were approved by script,
  `approval: recorded`). Say which one you are showing.
- **Simulated:** the plants run in one process; the stand-in model reads the pack's own sentences perfectly; the
  follow-up layer is built ahead of X4 and not measured; the outcome is "not yet checked".
- **Send back** the six run files of a `--routing` recording, with a note on the server (section 2). They hold no
  narrative, no path, no host or user name and no key; the run's own scans and the lint check that. `ledger.jsonl`
  holds HQ's own model calls only; a plant's usage is in `scorecard.json` `ledger.site_usage`, only as the
  k-suppressed usage summaries that crossed its Boundary (a plant's per-call ledger never leaves it). Never send the
  work directory (`--keep-workdir`): it holds the synthetic narratives and the canary manifest.
- **What the demo does not replace:** E2 (section 13) measures pushdown against central reading; X1 (section 11) is
  the blind planted-pattern test; the public replay (section 12) is the real-data result. Until they run, the demo's
  real-data line says "not yet measured".

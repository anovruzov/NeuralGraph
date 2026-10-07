# Founder runbook: week-1 measurements, E1, G0, X1 and the public replay

This runbook covers what you run on your own machines (STRATEGY sections 11.2 and 12):

- **E3**: latency and throughput of a model served inside a boundary;
- the **openFDA fetch**;
- **N1**: how often a device-event narrative carries information the coded fields lack;
- **freezing a domain pack** before any labelling;
- **E1**: whether a model inside the boundary extracts claims well enough, against a frontier reference;
- **G0**: whether planted text leaves a site through the Boundary (text only, synthetic data);
- **X1/X2**: the blind planted-pattern test of the frozen detectors against S, R (model-free), U and single-site
  baselines (synthetic, internal only; section 11);
- **the openFDA public replay** (STRATEGY section 9.3): signals frozen before any recall is opened (section 12).

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
transport schema) changed since `prereg`. Ctrl-C leaves `run.json` with `complete: false`; start a new run id.
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

Through a real model server instead of the fake (section 2). The routing file's extraction endpoint must declare
`"boundary": "any-simulated"`, because one machine plays every simulated site. The run is still synthetic and
measures nothing about the model; it only checks that the path through a real server leaks no text:

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
  caches' `manifest.json` (the pages only if asked; they are public data).

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
- `recalls.unmatched_product_code` lists recalls of codes with no event in the cache; `not_evaluable` recalls fall
  before the first evaluated week.

# Founder runbook, part 1: week-1 measurements

This runbook covers three things you run on your own machines in week 1 (STRATEGY sections 11.2 and 12):

- **E3**: latency and throughput of a model served inside a boundary;
- the **openFDA fetch**;
- **N1**: how often a device-event narrative carries information the coded fields lack.

None of these produced a number in the sandbox where the code was written. Model weights and api.fda.gov could not
be reached there, so every figure has to come from your runs.

## 0. Honesty rules (read these first)

1. **Never quote a number you did not measure.** If a run file does not contain it, it does not exist. Estimates
   in STRATEGY stay labelled as estimates until E3 replaces them.
2. **Label every result** with the endpoint, the host and the hardware. E3 writes the client host into `e3.json`;
   describe the server machine (model file, quantisation, GPU/CPU, server flags) with `--server-note`.
3. **A rehearsal is not a result.** A run against the fake server or a fake provider writes `"measurement": false`.
   Never quote it. Only runs with `"measurement": true` count.
4. **Say what the data is.** openFDA results are public data (`data_label: public`). MAUDE holds reportable events,
   not internal complaints. Synthetic text is labelled synthetic.
5. **Send back run files, never keys** (section 7).

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

## 7. What to send back

Send these files:

- `runs/e3/<run-id>/e3.json` and `runs/e3/<run-id>/ledger.jsonl`, with your note on the server setup;
- `<cache-dir>/manifest.json` (the pages only if asked; they are public data but large);
- `runs/n1/<run-id>/sample.json`, the labelled sheet and `runs/n1/<run-id>/narrative_gain.json`.

Never send:

- an API key, a `.env` file, or a shell history that contains a key;
- partner data of any kind (none is used in week 1).

The ledger holds numbers and labels only (no prompt, no reply, no record text), so it is safe to send. One label
comes from the server: `model_served` is the model id each server reported, kept only when it is a short plain
identifier. Look over that column before you send the ledger if your servers use names you would rather not share.

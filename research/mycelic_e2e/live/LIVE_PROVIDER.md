# Live local LLM for the Mycelic implementation (ENGINEER-C)

Written 2026-10-09 by ENGINEER-C (model `claude-sonnet-5-5`). Everything runs in this cloud container
(4 vCPU Intel Xeon @ 2.10 GHz with AVX512/AMX, 15 GB RAM, no GPU), outside the repository, under `/root/mycelic-live`.
No weights or binaries are in the repo; this directory holds text only (scripts, manifests, benchmark JSON, this note).

**Status: a real model is serving and the product's `ModelRouter` completed real tasks against it.**
Server: `http://127.0.0.1:8082/v1`, model Qwen3-4B-Instruct-2507 Q4_K_M, PID 8513, running in the background.

## 1. What was built

| item | value |
|---|---|
| source | `pip download --no-deps --no-binary :all: llama-cpp-python==0.3.16` (PyPI sdist, sha256 `34ed0f9bd9431af045bb63d9324ae620ad0536653740e9bb163a2e1fcb973be6`), unpacked to `/root/mycelic-live/src/llama-cpp-python-0.3.16` |
| vendored llama.cpp | commit `4227c9be4268ac844921b90f31595f81236bd317` (2025-08-14, "CUDA: fix negative KV_max values in FA (#15321)"); `llama-server --version` prints `version: 1 (4227c9b)`, `/props` reports `build_info: b1-4227c9b` (the build number shows as 1) |
| build | `scripts/build.sh`: cmake Release, `-DGGML_NATIVE=ON` (CPU only, CUDA/Metal off, no curl), targets `llama-server` and `llama-bench`, `nice -n 15`, `-j 3`; log `/root/mycelic-live/logs/build.log` |
| binary | `/root/mycelic-live/build/bin/llama-server` (sha256 `9b54383838a113b41511a3ff67878c745ffc1a1de7c50f7b8bacff1a36eef84b`; host-specific because of `-march=native`) |

Differences from the previous session's llama.cpp (a newer one): this older build has `-fa` as a plain switch (no `on|off|auto`),
no `--cache-ram`, no `--no-kv-unified` (the default is already one KV region per slot; `--kv-unified` is opt-in).
`serve.sh` here is adapted accordingly.

## 2. Model files (Docker Hub registry HTTP API, repository `ai/qwen3`)

Token from `auth.docker.io` (`scope=repository:ai/qwen3:pull`), manifest from `registry-1.docker.io/v2/ai/qwen3/manifests/<tag>`, blob
from `/v2/ai/qwen3/blobs/<digest>` (script `scripts/pull_blob.sh`). Manifests are saved in `data/`. Notes:
the manifests are OCI model artifacts with ONE layer of mediaType `application/vnd.cncf.model.weight.v1.raw` (not a mediaType
containing `gguf`); the layer was chosen by its annotation `org.cncf.model.filepath` = `*.gguf`. The registry answered
`TOOMANYREQUESTS` (unauthenticated pull rate limit) on some manifest requests; retrying a few seconds later worked.
The tag for the 4B is lower case: `4b-instruct-2507-q4_K_M` (`4B-Instruct-2507-Q4_K_M` is not a tag).

| model | tag | file | size (bytes) | sha256 observed = manifest digest |
|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 Q4_K_M (annotation `ai.model.repo`: `unsloth/Qwen3-4B-Instruct-2507-GGUF`) | `ai/qwen3:4b-instruct-2507-q4_K_M` | `/root/mycelic-live/models/qwen3-4b-instruct-2507-q4_k_m.gguf` | 2497281120 | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` (matches the previous session's `3605803b…e597`) |
| Qwen3-1.7B Q4_K_M (`unsloth/Qwen3-1.7B-GGUF`) | `ai/qwen3:1.7b-q4_K_M` | `/root/mycelic-live/models/qwen3-1.7b-q4_k_m.gguf` | 1107409472 | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` (matches `b139949c…1897`) |

Both digests were computed with `sha256sum` on the downloaded files and equal the manifest layer digests.

## 3. Server (running)

* Chosen: **Qwen3-4B-Instruct-2507**, `127.0.0.1:8082`, `THREADS=2`, `NP=4` (8192 context cells per slot, 32768 total), PID **8513**
  (`/root/mycelic-live/logs/server-4b.pid`), log `/root/mycelic-live/logs/server-4b.log`, RSS about 6.3 GB.
* Exact command (started by `scripts/serve.sh 4b`, `nice -n 5`, `nohup`):

```
/root/mycelic-live/build/bin/llama-server -m /root/mycelic-live/models/qwen3-4b-instruct-2507-q4_k_m.gguf \
  --host 127.0.0.1 --port 8082 -t 2 -tb 2 -np 4 -c 32768 -cb -fa -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 \
  --jinja --no-webui --metrics
```

* Health: `curl -s http://127.0.0.1:8082/health` returns `{"status":"ok"}`; `GET /props` has `build_info`, `model_path`, `total_slots: 4`.
* Smoke test with thinking disabled: POST `/v1/chat/completions` with `"chat_template_kwargs":{"enable_thinking":false}` returned
  `{"capital": "Paris"}`, 7 completion tokens, `finish_reason: stop`. The 4B-Instruct-2507 model has no thinking mode, so the setting is accepted and has no effect.
* Also tried (as the task asked, because 4 concurrent streams were below 3 tok/s): the 1.7B with `scripts/serve.sh 1.7b` on `127.0.0.1:8081`,
  same `-t 2 -np 4`, with `--chat-template-kwargs {"enable_thinking":false}` set server-wide (the 1.7B does have a thinking mode and the product cannot
  send the per-request field, see section 5). It answered the same smoke prompt with no reasoning, then **was stopped** after benchmarking to leave the cores free.
  Start it again with `/root/mycelic-live/serve.sh 1.7b` (PID file `/root/mycelic-live/logs/server-1.7b.pid`).

**Stop:** `/root/mycelic-live/serve.sh stop 4b` (or `kill $(cat /root/mycelic-live/logs/server-4b.pid)`). `serve.sh stop` with no argument stops both.
Restart: `/root/mycelic-live/serve.sh 4b` (models stay on disk).

## 4. Measured throughput

Probe: `scripts/bench_tps.py` (distinct ~310-token prompt per request so no prompt-cache reuse, `max_tokens` 60, temperature 0, answer always
reaches 60 tokens, `finish_reason: length`). Per-request prefill and decode tok/s are llama.cpp's own `timings`; "aggregate" is completion tokens / wall time
over the whole batch (prefill included). **The machine was not idle**: other benchmark processes (two Python jobs at about 100% CPU each) were
running throughout, load average 2.7 to 3.9 on 4 vCPUs, so these are contended numbers; raw rows are in `data/`.

Qwen3-4B-Instruct-2507, `-t 2 -np 4` (`data/bench_4b_300tok_np4_t2.jsonl`, `..._rerun.jsonl`):

| concurrent | prompt / answer | wall per batch | per-stream decode tok/s | per-stream prefill tok/s | aggregate completion tok/s |
|---|---|---|---|---|---|
| 1 | 317 / 60 | 21.8 s, 21.1 s, 23.9 s | 4.43, 4.23, 3.80 | 37.1, 43.5, 37.5 | 2.75, 2.85, 2.51 |
| 4 | 309-317 / 60 | 54.5 s, 57.9 s, 69.0 s | mean 2.08, 2.04, 1.48 (individual 0.99 to 2.67; includes the other slots' prefill interleaved) | 12 to 39 | 4.41, 4.15, 3.48 |

Earlier probe with 630-token prompts (my first prompt generator was twice too long; `data/bench_4b_630tok_np4_t2.jsonl`): 1 stream 49.5 s and 34.1 s
(decode 3.8 and 4.1 tok/s), 4 streams 111 s and 104 s (aggregate 2.2 and 2.3 tok/s).

Qwen3-1.7B, `-t 2 -np 4` (`data/bench_1.7b_300tok_np4_t2.jsonl`):

| concurrent | wall per batch | per-stream decode tok/s | per-stream prefill tok/s | aggregate completion tok/s |
|---|---|---|---|---|
| 1 | 11.2 s, 11.4 s | 7.88, 7.71 | 90 | 5.38, 5.28 |
| 4 | 27.7 s, 30.6 s | mean 3.63, 3.58 (individual 2.25 to 4.37) | 25 to 80 | 8.66, 7.85 |

Reading: one 4B stream clears the 3 tok/s bar (3.8 to 4.4); four concurrent 4B streams do not (mean 1.5 to 2.1 per stream while prefills overlap,
about 4 tok/s in aggregate), so concurrency 4 buys roughly 1.5x aggregate over one stream, not 4x, on two shared threads.
Real product calls measured through the router (section 6): `answer_from_evidence` (344 prompt / 61 completion tokens) took 52.6 s
(server: prefill 33 tok/s, decode 1.44 tok/s at that moment); `evaluate_responses` (526 / 278 tokens) took 155.8 s (prefill 14 tok/s, decode 2.34 tok/s).
Plan with about 1.5 to 4 tok/s decode and 14 to 43 tok/s prefill per stream on this shared machine; a 1200-token `synthesize_discovery` or `aggregate_level`
reply can take 5 to 13 minutes, which is why the timeout below is raised.

## 5. How the product's router is pointed at it

Read from `mycelic/config.py`, `mycelic/models/router.py`, `mycelic/models/openai_provider.py`:

| env var | effect | value used |
|---|---|---|
| `MYCELIC_MODEL_LIGHT`, `MYCELIC_MODEL_STANDARD`, `MYCELIC_MODEL_HEAVY` | per-tier `"provider:model"` (split at the first colon; provider must be `fake`, `anthropic` or `openai`); `openai:<name>` is the OpenAI-compatible chat provider, `<name>` is sent verbatim as `"model"` in the request (llama-server ignores it) | `openai:qwen3-4b-instruct-2507` for all three |
| `OPENAI_BASE_URL` (fallback `LLM_BASE_URL`, else `https://api.openai.com/v1`) | base URL for ALL openai-provider tiers; the provider POSTs `{base}/chat/completions`, so it must end in `/v1` | `http://127.0.0.1:8082/v1` |
| `OPENAI_API_KEY` | sent as `Authorization: Bearer` only when non-empty; llama-server needs none | `local` (any non-empty string) |
| `MYCELIC_MODEL_MAX_PARALLEL` (default 4) | client-side semaphore per provider instance | 4 (= `-np 4`) |
| `MYCELIC_MODEL_TIMEOUT_SECONDS` (default 120) | total timeout per HTTP call (the provider retries 2 times on network/5xx/408/409/429) | 900 |
| `MYCELIC_EMBED_PROVIDER` (default `hash`) | `hash` = deterministic embedder, no endpoint needed; `openai` would call `{base}/embeddings` and needs llama-server `--embedding` | `hash` |

The same lines are in `live_env.sh`; use `set -a; . research/mycelic_e2e/live/live_env.sh; set +a`. `Settings` reads `os.environ` when `mycelic.config` is imported, so export
the variables before starting the process.

Limits to know:

* **One base URL for all tiers.** `DefaultModelRouter.from_settings` builds exactly one `OpenAICompatProvider` per provider name. There is no per-tier URL, so light, standard and heavy all hit the
  same server. Using the 1.7B for light and the 4B for the rest would need a small model-name routing proxy in front of ports 8081 and 8082 (not built). Escalation after two invalid replies therefore stays on the same model.
* **`chat_template_kwargs` cannot be set from env.** `OpenAICompatProvider` has an `extra_body` constructor argument that is merged into every request, but `from_settings` does not pass it and no environment variable fills it.
  It can only be set by constructing the provider in code (`OpenAICompatProvider(..., extra_body={"chat_template_kwargs": {"enable_thinking": False}})`
  and `DefaultModelRouter(providers={"openai": that}, ...)`). It does not matter for Qwen3-4B-Instruct-2507 (no thinking mode). For the 1.7B (or any Qwen3 hybrid model) the working route is the
  server flag `--chat-template-kwargs '{"enable_thinking":false}'`, which `serve.sh 1.7b` sets; the provider additionally strips any `<think>...</think>` left in `content`.
* JSON: `response_format` is sent only to `*.openai.com`; for this server the JSON-only instruction is appended to the system message. No grammar or schema is sent, so validity relies on the model plus the router's repair retry (not needed in the two verified calls).
* Sampling: `temperature` 0.0, `max_tokens` from each task spec (1200 by default); no seed.

## 6. Verification (real calls through the product's router)

`verify_live_provider.py` loads `mycelic.config.load_settings()` from the environment, builds `DefaultModelRouter.from_settings(settings)` (the constructor `mycelic.models.base.build_router` uses),
prints what the router resolved and runs `answer_from_evidence` (2-item evidence list, one relevant, one distractor) and, with `--evaluate`, `evaluate_responses` (two conflicting holder responses).

```
cd /home/user/ng-impl && set -a && . research/mycelic_e2e/live/live_env.sh && set +a && python3 research/mycelic_e2e/live/verify_live_provider.py --evaluate
```

Output (saved in full in `data/verify_run1.txt`, wall time 3 m 29 s, `RESULT: OK`):

```
settings.model_tiers : {"light": "openai:qwen3-4b-instruct-2507", "standard": "openai:qwen3-4b-instruct-2507", "heavy": "openai:qwen3-4b-instruct-2507"}
settings.openai_base_url: http://127.0.0.1:8082/v1 | api key set: True | max_parallel: 4 | timeout_s: 900.0 | embed: hash
router.describe()['tiers']: {"light": {"provider": "openai", "model": "qwen3-4b-instruct-2507"}, "standard": {...same...}, "heavy": {...same...}}

[answer_from_evidence] 52.6s ->
{ "answer": "The Atlas billing migration cut over on 2026-03-14 and was approved by Dana Okafor (finance lead).",
  "confidence": 1, "used_ref_ids": ["ev-101"], "no_evidence": false }

[evaluate_responses] 155.8s ->
{ "findings": [ {"text": "Atlas billing cut over on 2026-03-14.", ... "supporting_response_ids": ["r1"], "supporting_ref_ids": ["ev-101"]},
                {"text": "Atlas billing cut over on 2026-03-21.", ... "supporting_response_ids": ["r2"], "supporting_ref_ids": ["ev-207"]} ],
  "disagreements": [ {"summary": "Conflicting dates for Atlas billing cut over", ... "a_response_ids": ["r1"], "b_response_ids": ["r2"]} ],
  "relevance": 1.0, "freshness": {"r1": 0.01, "r2": 0.01} }

usage ledger totals: {"calls": 2, "input_tokens": 870, "output_tokens": 339, "cost_usd": 0.0, "failures": 0, ...}
  call: {"tier": "light", "purpose": "answer_from_evidence", "input_tokens": 344, "output_tokens": 61, "latency_ms": 52632, "ok": true, "error": null}
  call: {"tier": "standard", "purpose": "evaluate_responses", "input_tokens": 526, "output_tokens": 278, "latency_ms": 155847, "ok": true, "error": null}
RESULT: OK
```

Both replies validated against the task schemas on the first attempt (no repair, no escalation). Quality remarks, not scored: the `answer_from_evidence` answer was correct and ignored the distractor;
`evaluate_responses` found the date conflict but returned a `freshness` object instead of the declared `freshness_note` string (that key is optional in the schema check, so it passed), and
`confidence: 1` on the answer is high for a 2-item evidence list. One sample each; do not read this as an accuracy measurement.

## 7. Files

`/root/mycelic-live/` (not in git): `build/bin/llama-server`, `models/*.gguf`, `logs/` (build, server logs, pid files, raw benchmark JSON), `src/`, `dl/` (sdist, manifests).
In the repo (`research/mycelic_e2e/live/`): this file, `live_env.sh`, `verify_live_provider.py`, `scripts/{build.sh,serve.sh,pull_blob.sh,bench_tps.py}` (copies of the ones in `/root/mycelic-live`), `data/` (manifests, benchmark rows, verification output).

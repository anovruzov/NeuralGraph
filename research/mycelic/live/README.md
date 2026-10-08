# Mycelic with live LLM agents (local open-weight models)

This package runs the Mycelic benchmark with **real language-model agents** in place of the simulator's
extraction operator, on free local models served by llama.cpp's `llama-server`, and puts every live number next
to the simulated-operator run of the same system on the same world. No paid API and no API key is involved.

| role | model | container run (Linux, CPU) | Mac run (Metal) |
|---|---|---|---|
| user (edge) agents | Qwen3-1.7B, Q4_K_M GGUF | Docker Hub `ai/qwen3:1.7b-q4_K_M`, sha256 `b139949c…1897` | Hugging Face, see below |
| A2 central reader ("kernel" model) | Qwen3-4B-Instruct-2507, Q4_K_M GGUF | Docker Hub `ai/qwen3:4b-instruct-2507-q4_K_M`, sha256 `3605803b…e597` | Hugging Face, see below |

Results so far: `RESULTS.md` in this directory.

## What is real and what is still simulated

**Real (a language model decides):**
* **Edge cognition.** Every user agent makes ONE model call over its own notes and returns one compact line
  per note, `<note number> <predicate> <subject entity> <ok|not>` (`agents.EdgeAgent`, prompt `edge-v2`).
  Predicate, subject entity and polarity are the model's reading of the text. The prompt lists the 50 predicate
  *names*, each with a one-line plain-language description written for this package, the catalog naming
  convention ("the subject is the first entity name after the event phrase") and the negation convention ("a
  final `not` denies or retracts the event"). It does **not** contain the generator's synonym lists
  (`corpus.PRED_SURFACE`); a test checks that no causal surface phrase occurs in the prompt.
* **A2_live.** The kernel model reads the observable-prefiltered notes (records whose rendered text contains a
  causal predicate phrase: `systems._lexical_index` in its observable mode) in chunks of 40 notes, with the same
  prompt and output format. Its claims replace the simulated extraction inside the unchanged
  `chunked_long_context` (`pre_ex=`).

**Code, observable, by design (not a model call):**
* output grammar (GBNF): predicate names restricted to the 50-name enum, note numbers to the agent's own
  indices, an explicit polarity word; the entity is any lowercase token, optionally followed by a second one;
* entity string → entity id by exact match against the enterprise catalog (`corpus.entities`): the subject is
  the first string the model wrote that is a catalog name; anything else is dropped, and a near-miss name maps to
  that other entity, as it would in a deployment. A repeated note number keeps its first line;
* day `t` from the note's `[dNNN]` header (the simulator copies it too; exact by construction);
* the **echo signature** from the note text (`notes.echo_signature`): the body after the header, without a
  trailing `not` (re-appended), without its final remaining token (the renderer paraphrases the last filler
  word per note), and without catalog names after the first (secondary mentions are context a re-reporter adds),
  hashed with sha256. On this generator's template text **near-duplicate detection is an easy problem**: echoes
  repeat the event's phrase, subject and filler verbatim. The evaluator measures the recipe against true event
  ids (`signature_*` rows); real forwarded or paraphrased reports would be much harder;
* `spurious` = False for every live claim (the evaluator measures live precision against the notes instead);
* routing, sketches, merging, triage, descents and question answering from first-pass claims: the existing
  Mycelic code, unchanged. `systems.user_extract(ex=...)` is the only entry point, so `HierRunner` and
  `central_triage` consume the live user layer exactly as they consume the simulated one.

**Still simulated (stated in every result):**
* **kernel synthesis checks** (O3 in `docs/mycelic_v5/ORACLE_AUDIT.md`): causal, temporal, dedup, entity and
  contradiction checks are coins at the kernel tier, executed exactly when the coin succeeds. An LLM kernel judge
  is a later stage;
* hallucination injection (O3h), the abstraction coins of the hierarchy's middle levels (O4), the routing and
  question-targeting coins (O5, O6), and the rankers fitted on simulator gold (O9);
* the simulator's cost meter (`compute_units`, `wall_seconds`). Real calls, tokens, slot-cache hits and wall
  time of the live calls are reported next to it (`usage` rows; `llm_*` columns of the live system rows).

So a live-vs-sim difference measures **the extraction operator only**: a real model reading the notes versus
the `small-7b` (edge) or `frontier-plus` (A2) coin-flip operator, with everything downstream identical.

## Privacy and leakage

* `notes.NoteStore` renders every user's notes ONCE with `Corpus.text` and keeps no reference to the corpus. An
  agent receives only `(agent_id, [(note_index, text)])` for its own notes. The prompt shows each note's body
  (after `[dNNN] author ::`); day and author are read from the same rendered text by code. The
  note-index → record-id map (the evidence pointer) never leaves the code.
* `live/score.py` is the only module that reads ground truth (true tuples, event ids, gold facet records), after
  the agents answered. `test_leakage` scans `live/*.py`; every site there is a SCORING row in
  `leakage_allowlist.json`.
* `test_live.py`: an agent call's messages are exactly the constant system prompt plus that agent's numbered
  note bodies (no author ids, no other agent's text); scrambling every hidden record field and poisoning the gold
  objects after rendering leaves every prompt byte-identical; rendering does not read the never-rendered fields.

## Stages, seeds and outputs

One command per stage; every stage is resumable (rerun the same command after an interruption: finished calls
replay from the cache).

| stage | users | seed | agents | systems |
|---|---|---|---|---|
| `--stage smoke` | 400 (429 agents) | 701 | first 40 | extraction quality only |
| `--stage 400` | 400 | 702 | all | H_mycelic_full, H_mycelic_prev, B4_central_triage, A2_live |
| `--stage 2000` | 2,000 | 703 | all | same |
| `--stage 10000` | 10,000 | 704 | all | same |

LIVE seeds are **700–709**, development data, disjoint from every protocol role (TUNE 500–599, DEV-VALIDATION
600–649, TRANSFER 650–679) and never the sealed final seeds 3000–3029; `run.py` refuses any other seed. Seed
700 is used by the throughput benchmark only; prompt and format changes were made on seed 701 only.

A2_live runs when a kernel endpoint is configured; otherwise the stage runs the other systems and says so.
After the first 50 live agent calls (and the first 20 A2 chunks) a stage prints `THROUGHPUT SELF-CHECK` with the
projected wall time of the rest of the stage and of every later stage; the projection is also written as a row.

Each run writes `<out-dir>/stage_<stage>_s<seed>.jsonl` (rows `meta`, `usage`, `diag`, `extraction`, `gap`,
`system`, `projection`) and a `.md` summary (live-vs-sim system table with real token counts, the
operator-gap tables, projections). **Every row carries `run_env`**: machine (platform, CPU / chip, cores,
memory), for each role the backend kind, URL and URL kind (loopback / private-network / remote), llama.cpp build
and the served model file's sha256, plus the git revision and prompt version.

Configuration (flag, or environment variable):

| flag | env | default |
|---|---|---|
| `--edge-url` / `--kernel-url` | `MYCELIC_EDGE_URL` / `MYCELIC_KERNEL_URL` | none |
| `--edge-model` / `--kernel-model` (local GGUF, for the sha256) | `MYCELIC_EDGE_MODEL` / `MYCELIC_KERNEL_MODEL` | the server's `/props` model path |
| `--concurrency` / `--kernel-concurrency` | `MYCELIC_EDGE_NP` / `MYCELIC_KERNEL_NP` | the server's slot count |
| `--out-dir` | `MYCELIC_LIVE_OUT_DIR` | `research/mycelic/artifacts/live` |
| `--cache-dir` | `MYCELIC_LIVE_CACHE_DIR` | `<out-dir>/cache` |

* **Record/replay cache**: every call is keyed by sha256(model file sha256 + request parameters + messages) in
  `<cache-dir>/<role>-<model sha256 prefix>.jsonl`. A rerun, or `--replay-only` (never contacts a server), is
  bit-for-bit identical. Decoding is greedy (`temperature 0`, `top_k 1`); with parallel slots llama.cpp's batched
  numerics can differ slightly between batch compositions, which is why the cache, not the sampler, is the
  reproducibility guarantee.
* Qwen3's thinking mode is disabled through the chat template (`chat_template_kwargs {"enable_thinking":
  false}`); verified on the container's server: no reasoning content, the first token is the answer (with
  `true` the same request returns reasoning).
* The harness needs llama.cpp's `llama-server` (per-request GBNF `grammar`); a preflight (`GET /props`) refuses
  any other endpoint.
* `--mock` runs the whole pipeline with `backend.MockBackend`, a deterministic lexical stub. Its rows are
  labelled `MOCK (deterministic lexical stub, NOT an LLM)` and are never an LLM result.
* `python3 -m research.mycelic.live.package_results` bundles the results and the cache separately (below).
* Tests: `python3 -m unittest research.mycelic.live.test_live` (MockBackend and a local fake HTTP server; about
  7 s).

## Run it on a Mac (Apple Silicon, llama.cpp + Metal)

Python 3.10+ with numpy is all the harness needs; `mac_setup.sh` sets that up in a venv.

1. **Power and heat.** Plug the Mac in. The MacBook Air is fanless and throttles under sustained load, so wrap
   every stage in `caffeinate -i` (keeps it awake) and expect the throughput self-check's projection to drift as
   the machine heats up.
2. **Servers.** From the repository root:

   ```sh
   research/mycelic/live/mac_setup.sh          # or: ... edge   /   ... kernel   /   ... stop
   ```

   It installs llama.cpp with `brew install llama.cpp` if `llama-server` is missing, creates
   `~/mycelic-live/venv` with numpy, resolves the two Hugging Face repos and starts one server per role with
   Metal offload, per-slot KV caches in q8_0 and flash attention:

   ```sh
   llama-server -hf Qwen/Qwen3-1.7B-GGUF:Q4_K_M --port 8081 -ngl 99 -np 8 -c 65536 \
     --no-kv-unified -fa on -ctk q8_0 -ctv q8_0 -cb --jinja --metrics --cache-ram 1024
   llama-server -hf <kernel repo>:Q4_K_M       --port 8082 -ngl 99 -np 4 -c 16384 \
     --no-kv-unified -fa on -ctk q8_0 -ctv q8_0 -cb --jinja --metrics --cache-ram 1024
   ```

   * Edge repo: `Qwen/Qwen3-1.7B-GGUF` (Qwen's own GGUF repo); `-hf <repo>:Q4_K_M` is llama.cpp's
     `<user>/<model>[:quant]` syntax and downloads into llama.cpp's cache on first start.
   * Kernel repo: `Qwen/Qwen3-4B-Instruct-2507-GGUF` was requested, but an official Qwen GGUF repo for the 2507
     instruct model **could not be confirmed** (Hugging Face is unreachable from the build container). The script
     therefore asks the Hugging Face API, in order, `Qwen/Qwen3-4B-Instruct-2507-GGUF`,
     `unsloth/Qwen3-4B-Instruct-2507-GGUF`, `lmstudio-community/Qwen3-4B-Instruct-2507-GGUF`,
     `bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF`, and serves the first that lists a `Q4_K_M` `.gguf` file
     (the edge model likewise falls back to `ggml-org/`, `unsloth/`, `bartowski/` repos). Override with
     `EDGE_HF=<repo>:Q4_K_M` / `KERNEL_HF=<repo>:Q4_K_M`.
   * **The Hugging Face files may differ byte-wise from the Docker Hub `ai/qwen3` files** the container results
     used (different conversion or quantisation runs). Every result row records the served file's sha256
     (`run_env.edge.model_sha256`, `run_env.kernel.model_sha256`) and the call cache is keyed on it, so Mac and
     container results are never mixed.
   * Memory: about 1.1 + 2.5 GB of weights and 3.7 + 1.2 GB of KV cache, roughly 8.5 GB of the 16 GB. Under
     memory pressure run `mac_setup.sh edge`, finish the edge phase of a stage, then `mac_setup.sh kernel` and
     rerun the stage with `--edge-replay-only` and `MYCELIC_EDGE_MODEL=<the edge model file printed by
     mac_setup.sh>` (its edge calls replay from the cache, which is keyed on that file's sha256). Ports, slots and context are environment variables
     (`EDGE_PORT`, `EDGE_NP`, `EDGE_CTX_PER_SLOT`, `KERNEL_…`).
   * Ollama and LM Studio also serve OpenAI-compatible endpoints, but they ignore llama.cpp's per-request
     `grammar` and `chat_template_kwargs`; the harness refuses them at preflight rather than run unconstrained.
3. **Run**, one command per stage:

   ```sh
   export MYCELIC_EDGE_URL=http://127.0.0.1:8081
   export MYCELIC_KERNEL_URL=http://127.0.0.1:8082
   PY=~/mycelic-live/venv/bin/python
   caffeinate -i $PY -m research.mycelic.live.run --stage smoke    # 40 agents, seed 701
   caffeinate -i $PY -m research.mycelic.live.run --stage 400      # 429 agents, seed 702
   caffeinate -i $PY -m research.mycelic.live.run --stage 2000     # seed 703
   caffeinate -i $PY -m research.mycelic.live.run --stage 10000    # seed 704
   ```

   Read the `THROUGHPUT SELF-CHECK` line after the first 50 agents of the smoke stage: it projects the wall time
   of every later stage at the measured rate. An interrupted stage resumes when the same command is rerun.
4. **Package**:

   ```sh
   $PY -m research.mycelic.live.package_results
   ```

   writes `research/mycelic/artifacts/live/packages/mycelic_live_results_<host>_<stamp>.tar.gz` (the JSONL and
   MD results plus `MANIFEST.json` with each file's sha256, stage, seed, prompt version and run environment;
   small, commit or attach it) and `mycelic_live_cache_<host>_<stamp>.tar.gz` (the replay cache; large at 2,000+
   agents; keep it local, restore it into the cache directory to replay). The result files in
   `research/mycelic/artifacts/live/` can also be committed directly; `cache/` and `packages/` are gitignored.

## Run it in the Linux container (CPU)

```sh
export LIVE_ROOT=<dir with runtime/llama.cpp/build/bin and models/>
THREADS=4 NP=8 research/mycelic/live/scripts/serve.sh edge      # :8081 (llama.cpp built from source)
research/mycelic/live/scripts/bench.sh edge "4" "4 8" 8           # llama-bench + end-to-end, seed 700
export MYCELIC_EDGE_URL=http://127.0.0.1:8081 MYCELIC_EDGE_MODEL=$LIVE_ROOT/models/qwen3-1.7b-q4_k_m.gguf
python3 -m research.mycelic.live.run --stage smoke
python3 -m research.mycelic.live.run --stage 400
# A2_live afterwards (edge calls replay from the cache):
research/mycelic/live/scripts/serve.sh stop edge
THREADS=4 NP=4 research/mycelic/live/scripts/serve.sh kernel    # :8082
MYCELIC_KERNEL_URL=http://127.0.0.1:8082 python3 -m research.mycelic.live.run --stage 400 --edge-replay-only
```

On a shared CPU, use no more llama.cpp threads than idle cores: decode collapsed from 8 to 0.6 tokens/s when
three threads competed with two other busy processes on this 4-vCPU machine.

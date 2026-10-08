# Mycelic with live LLM agents (local open-weight models, CPU)

This package runs the Mycelic benchmark with **real language-model agents** in place of the simulator's
extraction operator, on free local models served by llama.cpp, and puts every live number next to the
simulated-operator run of the same system on the same world. No paid API and no API key is involved.

| role | model | served by |
|---|---|---|
| user (edge) agents | Qwen3-1.7B, Q4_K_M GGUF (sha256 `b139949c…1897`) | `llama-server`, CPU |
| A2 central reader ("kernel" model) | Qwen3-4B-Instruct-2507, Q4_K_M GGUF (sha256 `3605803b…e597`) | `llama-server`, CPU |

## What is real and what is still simulated

**Real (a language model decides):**
* **Edge cognition.** Every user agent makes ONE model call over its own notes and returns one compact line
  per note: `<note number> <predicate> <subject entity>[ not]` (`agents.EdgeAgent`). Predicate, subject entity and
  negation are the model's reading of the text. The prompt lists the 50 predicate *names* with a one-line
  plain-language description each (written for this package), the catalog naming convention ("the subject is
  the first entity name after the event phrase") and the negation convention ("a final `not` denies or retracts
  the event"). It does **not** contain the generator's synonym lists (`corpus.PRED_SURFACE`); a test checks that no
  causal surface phrase occurs in the prompt.
* **A2_live.** The kernel model reads the observable-prefiltered notes (records whose rendered text contains a
  causal predicate phrase, `systems._lexical_index` in its observable mode) in chunks of 40 notes, with the same
  prompt and output format, and its claims replace the simulated extraction inside the unchanged
  `chunked_long_context` (`pre_ex=`).

**Code, observable, by design (not a model call):**
* entity string → entity id by exact match against the enterprise catalog (`corpus.entities`); anything else
  is dropped, and a near-miss name maps to that other entity, as it would in a deployment;
* predicate name → id; the output grammar (GBNF) restricts names to the 50-name enum and note numbers to the
  agent's own indices; a repeated note number keeps its first line;
* day `t` from the note's `[dNNN]` header (this is also what the simulator copies; it is exact by construction);
* the **echo signature** from the note text (`notes.echo_signature`): the body after the header, without a
  trailing `not` (re-appended), without its final remaining token (the renderer paraphrases the last filler
  word per note), and without catalog names after the first (secondary mentions are context a re-reporter adds),
  hashed with sha256. On this generator's template text **near-duplicate detection is an easy problem**: echoes
  repeat the event's phrase, subject and filler verbatim. The evaluator measures the recipe against true event
  ids (`signature_*` rows); real forwarded or paraphrased reports would be much harder;
* `spurious` = False for every live claim (the evaluator measures live precision against the notes instead);
* routing, sketches, merging, triage, descents and question answering from first-pass claims: the existing
  Mycelic code, unchanged (`systems.user_extract(ex=...)` is the only entry point, so `HierRunner` and
  `central_triage` consume the live user layer exactly as they consume the simulated one).

**Still simulated (stated in every result):**
* **kernel synthesis checks** (O3 in `docs/mycelic_v5/ORACLE_AUDIT.md`): causal / temporal / dedup / entity /
  contradiction checks are coins at the kernel tier, executed exactly when the coin succeeds; an LLM kernel judge
  is a later stage;
* hallucination injection (O3h), the abstraction coins of the hierarchy's middle levels (O4), the routing and
  question-targeting coins (O5, O6), and the rankers fitted on simulator gold (O9);
* the simulator's cost meter (`compute_units`, modelled wall time). Real token counts, cache hits and wall time
  of the live calls are reported next to it (`usage` rows).

So a live-vs-sim difference measures **the extraction operator only**: a real 1.7B model reading the notes
versus the `small-7b` coin-flip operator, everything downstream identical.

## Privacy and leakage

* `notes.NoteStore` renders every user's notes ONCE with `Corpus.text` and keeps no reference to the corpus. An
  agent receives only `(agent_id, [(note_index, text)])` for its own notes. The prompt shows each note's body
  (after `[dNNN] author ::`); day and author are read from the same rendered text by code. The
  note-index → record-id map (the evidence pointer) never leaves the code.
* `live/score.py` is the only module that reads ground truth (the true tuple, event ids, gold facet records),
  after the agents answered. `test_leakage` now scans `live/*.py`; every new site is a SCORING row in
  `leakage_allowlist.json`.
* `test_live.py`: an agent call's messages are exactly the constant system prompt plus that agent's numbered
  note bodies (no author ids, no other agent's text); scrambling every hidden record field and poisoning the gold
  objects after rendering leaves every prompt byte-identical; rendering does not read the never-rendered fields.

## Reproduce

```sh
# 1. servers (threads: no more than idle cores; decode collapses when llama.cpp threads are oversubscribed)
THREADS=2 NP=8 CTX=49152 research/mycelic/live/scripts/serve.sh edge     # :8081
THREADS=2 NP=4 CTX=24576 research/mycelic/live/scripts/serve.sh kernel   # :8082 (run the two one at a time on 4 cores)
# 2. throughput (llama-bench + end-to-end through the server on real prompts, seed 700)
research/mycelic/live/scripts/bench.sh edge "2" "1 4 8" 8
# 3. smoke (prompt development seed 701, first 40 agents; extraction quality only)
python3 -m research.mycelic.live.run --scale 400 --seed 701 --edge-url http://127.0.0.1:8081 \
   --edge-model <models>/qwen3-1.7b-q4_k_m.gguf --concurrency 8 --max-agents 40 \
   --out research/mycelic/artifacts/live/smoke701.jsonl
# 4. full 400-user run, paired with the simulated operators
python3 -m research.mycelic.live.run --scale 400 --seed 702 --edge-url http://127.0.0.1:8081 \
   --edge-model <models>/qwen3-1.7b-q4_k_m.gguf --concurrency 8 \
   --systems H_mycelic_full,H_mycelic_prev,B4_central_triage \
   --out research/mycelic/artifacts/live/live400_702.jsonl
#    then A2_live with the kernel server (the edge calls replay from the cache):
python3 -m research.mycelic.live.run ... --kernel-url http://127.0.0.1:8082 \
   --kernel-model <models>/qwen3-4b-instruct-2507-q4_k_m.gguf --kernel-concurrency 4 \
   --systems H_mycelic_full,H_mycelic_prev,B4_central_triage,A2_live
```

* Every call goes through a **record/replay cache** keyed by sha256(model file sha256 + request parameters +
  messages) in `research/mycelic/artifacts/live/cache/<role>-<model>.jsonl`. An interrupted run restarts where it
  stopped; a rerun (or `--replay-only`, which never contacts a server) is bit-for-bit identical. Greedy decoding
  (`temperature 0`, `top_k 1`); with parallel slots llama.cpp's batched numerics can differ slightly between
  batch compositions, which is why the cache, not the sampler, is the reproducibility guarantee.
* Qwen3's thinking mode is disabled through the chat template (`chat_template_kwargs {"enable_thinking": false}`,
  verified: the server returns no reasoning content and the first token is the answer).
* `--mock` runs the whole pipeline with `backend.MockBackend`, a deterministic lexical stub. Its rows are
  labelled `MOCK (deterministic lexical stub, NOT an LLM)` and are never an LLM result.
* `python3 -m unittest research.mycelic.live.test_live` (MockBackend and a local fake HTTP server; < 1 min).

## Seeds

LIVE seeds are **700–709**, development data, disjoint from every protocol role (TUNE 500–599, DEV-VALIDATION
600–649, TRANSFER 650–679) and never the sealed final seeds 3000–3029; `run.py` refuses any other seed.
* 700: throughput benchmarks only;
* 701: prompt and format development (smoke runs). Prompt changes are made on 701 only;
* 702: the full 400-user live run;
* 703–709: reserved (replications, 2,000 / 10,000 users).

## Outputs

`run.py --out X.jsonl` writes `X.jsonl` (rows: `meta`, `usage`, `diag`, `extraction`, `gap`, `system`, `eta`) and
`X.md` (the live-vs-sim system table, the operator-gap tables and the ETAs). `score.gap_table` puts three
columns side by side: the simulator's nominal tier parameter, the simulated operator measured on the same notes,
and the live model measured on the same notes.

Results of the runs made so far: see `RESULTS.md` in this directory.

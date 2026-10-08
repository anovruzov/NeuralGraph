# Live Mycelic: results so far (Linux container, CPU)

Machine: 4 vCPU Intel Xeon @ 2.1 GHz (AVX-512, AMX), 15 GB RAM, shared with other agents; llama.cpp built from
source (build `b1-fc9ce6b`), CPU only. Models: Qwen3-1.7B Q4_K_M (edge, sha256 `b139949c…1897`) and
Qwen3-4B-Instruct-2507 Q4_K_M (A2 reader, sha256 `3605803b…e597`), both from Docker Hub `ai/qwen3`.
Every number below that says *live* comes from a real model call; every number that says *sim* comes from the
simulator's operators on the same world. Kernel synthesis checks are simulated coins in **both**.

Raw files: `research/mycelic/artifacts/live/` (`stage_*_s*.jsonl` / `.md`, `bench/`).

## 1. Throughput (choose `-np` and threads)

| measurement | result |
|---|---|
| llama-bench, 4 threads, idle machine | prefill 200–218 tok/s (pp512, AMX repack); decode 13.3–15.8 tok/s (tg64) |
| llama-bench, small batches, 4 threads | pp4 44.5, pp8 72.7, pp16 139, pp64 202 tok/s (without AMX repack: pp8 50, pp64 170) |
| llama-bench, 2 threads, 2 other busy processes | prefill 105 tok/s, decode 8 tok/s; **3 threads: decode 0.6 tok/s** (oversubscribed threads collapse) |
| server decode, 1 stream (64 tokens) | 10.3 tok/s |
| server decode, 8 streams | 26.5–30 tok/s aggregate (3.5 tok/s per stream); grammar costs little once prefill is excluded; KV q8_0 vs f16 and flash attention on/off make no difference. The tied 151k-vocabulary output matrix (q6_K, not AMX-repacked) is computed per stream and caps the batching gain. |
| end to end, 8 agents of seed 700 | np=4: 48.6 s/agent; np=8: 39.1 s/agent (8 agents on 8 slots is tail-dominated) |
| end to end, steady state (smoke and 400 run, np=8, 4 threads) | ~30 s per agent wall, ~0.8–0.9 s per note, ~11 completion tok/s aggregate (decode-bound: ~10 output tokens per note against ~25 input tokens, and prefill is 15× cheaper per token than decode on this CPU) |

Chosen: **4 threads, `-np 8`, per-slot KV (8,192 cells, q8_0), flash attention**, used for the smoke and the
400-user run. Unified KV (`--kv-unified`) was no faster.

## 2. Smoke (seed 701, first 40 agents, 1,411 notes) and the format fix

**edge-v1** (`<n> <predicate> <entity>[ not]`, first 7 agents, 261 notes): exact (predicate, entity, polarity)
on only **50.6%**. Two format artefacts, not reading errors: under the grammar, a model that wanted to go on
copying the note's second entity could continue only with ` not` or by gluing names with `-`. Result: false or
missing negations on 21% of notes (one agent marked every note `not`) and glued or non-entity strings
(`cobalt-lumen`, `logged`) on 27%.

**edge-v2** (explicit `ok|not` on every line, optional second entity string ignored by the code, subject = first
catalog name the model wrote; frozen for every later run):

| quantity (40 agents, 1,411 notes) | small-7b nominal | sim small-7b, same notes | live Qwen3-1.7B |
|---|---|---|---|
| claim emission rate | 0.701 | 0.709 | **0.974** |
| predicate error, causal notes, entity right | 0.224 (chain neighbours only) | 0.088 | **0.339** |
| entity fidelity, predicate right | 0.843 | 0.841 | **0.996** |
| spurious claims per note | 0.183 | 0.181 | **0.019** |
| polarity accuracy | 1.000 (exact) | 0.993 | **0.956** |
| negated notes marked `not` | 1.0 | 0.80 | **0.047 (2 of 43)** |
| exact-tuple precision | – | 0.653 | **0.822** |
| exact-tuple recall, all notes | – | 0.581 | **0.801** |
| exact-tuple recall, causal notes | – | 0.572 | **0.612** |
| exact-tuple recall, gold facet notes (22) | – | 0.682 | **0.591** |
| echo-signature pair recall (precision 1.000 both) | event id for 92% | 0.803 | **1.000** (text recipe) |

The live model's errors have a different **shape** from the simulator's: almost no misses, spurious claims or
entity slips, but predicate confusions on causal notes (52% causal → routine, 38% to another chain, only 6.5%
to a chain neighbour, the only slip the simulator models; "52%" counts confusions between a causal and a routine predicate in either direction) and **it almost never reads a negation**. A
polarity-first variant (`<n> <ok|not> <predicate> <entity>`) was tried on the 8 most negation-rich smoke agents
(399 notes): 2 of 21 negations found, exact 0.712. It was rejected; edge-v2 stays.

## 3. 400 users, seed 702: live vs simulated operators

(filled in when the run completes)

## 4. ETA at the measured throughput

(filled in when the run completes)

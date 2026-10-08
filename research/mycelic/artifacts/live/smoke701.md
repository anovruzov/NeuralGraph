# Live Mycelic run `smoke701`
scale 400 (users 429), seed 701, agents run 40/429, notes 1411.
edge backend: edge:qwen3-1.7b-q4_k_m.gguf; kernel backend: None.

**edge usage**: 40 calls (0 replayed), prompt 68875 tok (34343 from slot cache), completion 14326 tok, phase wall 1293.8 s, recorded latency sum 9970 s, aggregate 11.5 completion tok/s.

**edge parse diagnostics**: notes 1411, lines 1411, malformed 0, bad_index 0, dup_index 0, bad_pred 0, bad_entity 36, kept 1375, truncated 0

## Operator gap: edge extraction (small-7b tier vs live edge)

| quantity | small-7b nominal | sim small-7b (measured) | live edge (measured) | live - sim |
|---|---|---|---|---|
| claim emission rate | 0.701 (P(a note yields a claim)) | 0.709 | 0.974 | +0.266 |
| predicate error, causal notes, entity right | 0.224 (chain-neighbour slips only) | 0.088 | 0.339 | +0.251 |
| entity fidelity, predicate right | 0.843 (per (author, entity), same-stem partner) | 0.841 | 0.996 | +0.155 |
| predicate error, all claims | - | 0.223 | 0.145 | -0.078 |
| entity fidelity, all claims | - | 0.673 | 0.977 | +0.303 |
| spurious claims / note | 0.183 (1 - extract_precision) | 0.181 | 0.019 | -0.162 |
| polarity accuracy | 1.000 (exact) | 0.993 | 0.956 | -0.037 |
| exact-tuple precision | - | 0.653 | 0.822 | +0.168 |
| exact-tuple recall, all notes | - | 0.581 | 0.801 | +0.220 |
| exact-tuple recall, causal notes | - | 0.572 | 0.612 | +0.041 |
| exact-tuple recall, gold facet notes | - | 0.682 | 0.591 | -0.091 |
| exact-tuple recall, rare-pattern facet notes | - | 0.500 | 0.750 | +0.250 |
| echo signature pair precision | event id for 92% of claims | 1.000 | 1.000 | +0.000 |
| echo signature pair recall | event id for 92% of claims | 0.803 | 1.000 | +0.197 |

## ETA at measured throughput (edge extraction only)

* 2000 users (1999 agents): 18.9 h (883.2 ms per note at concurrency 8 (recorded latencies))
* 10000 users (9999 agents): 94.6 h (883.2 ms per note at concurrency 8 (recorded latencies))

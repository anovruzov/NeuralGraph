# Live Mycelic run `stage_smoke_s701`
scale 400 (users 429), seed 701, agents run 40/429, notes 1411.
edge backend: edge:qwen3-1.7b-q4_k_m.gguf; kernel backend: None; prompt edge-v2.

**edge usage**: 40 calls (40 replayed), prompt 68875 tok (34343 from slot cache), completion 14326 tok, phase wall 0.0 s, recorded latency sum 9970 s, aggregate 5.7 completion tok/s.

**edge parse diagnostics**: notes 1411, lines 1411, malformed 0, bad_index 0, dup_index 0, bad_pred 0, bad_entity 36, kept 1375, truncated 0, run_env {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.10GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'edge': {'backend_kind': 'llama-server', 'url': 'http://127.0.0.1:8081', 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897', 'model_file': 'qwen3-1.7b-q4_k_m.gguf', 'llama_cpp': None, 'server_slots': 0, 'concurrency': 4}, 'kernel': None, 'git': '31e5dc0', 'prompt_version': 'edge-v2', 'harness': 'mycelic-live/2'}

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

_Run environment: Intel(R) Xeon(R) Processor @ 2.10GHz (x86_64, 4 cpus, 15.7 GB), Linux-6.18.44-fc-v80-x86_64-with-glibc2.39; edge llama-server llama.cpp None model sha256 b139949c5bd74937; git 31e5dc0._

# Live Mycelic run `stage_400_s702_A2_matched`
scale 400 (users 429), seed 702, agents run 429/429, notes 16233.
edge backend: edge:qwen3-1.7b-q4_k_m.gguf; kernel backend: kernel:qwen3-1.7b-q4_k_m.gguf; prompt edge-v2.

**edge usage**: 429 calls (429 replayed), prompt 754496 tok (463316 from slot cache), completion 164915 tok, phase wall 0.1 s, recorded latency sum 143785 s, aggregate 9.2 completion tok/s.

**edge parse diagnostics**: notes 16233, lines 16231, malformed 0, bad_index 0, dup_index 0, bad_pred 0, bad_entity 683, kept 15548, truncated 3, run_env {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.80GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'edge': {'backend_kind': 'llama-server', 'url': 'http://127.0.0.1:8081', 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897', 'model_file': 'qwen3-1.7b-q4_k_m.gguf', 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'concurrency': 8, 'replay_only': False, 'calls_recorded_on': [{'recorded': None, 'calls': 296, 'replayed_here': 296}, {'recorded': {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.10GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'}, 'calls': 34, 'replayed_here': 34}, {'recorded': {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.80GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'}, 'calls': 99, 'replayed_here': 99}]}, 'kernel': {'backend_kind': 'llama-server', 'url': 'http://127.0.0.1:8081', 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897', 'model_file': 'qwen3-1.7b-q4_k_m.gguf', 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'concurrency': 8, 'replay_only': False, 'calls_recorded_on': [{'recorded': {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.80GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'}, 'calls': 143, 'replayed_here': 0}]}, 'git': '9d4711f', 'prompt_version': 'edge-v2', 'harness': 'mycelic-live/2'}

**kernel-reader usage**: 143 calls (0 replayed), prompt 255116 tok (152023 from slot cache), completion 57565 tok, phase wall 5307.7 s, recorded latency sum 42208 s, aggregate 10.9 completion tok/s.

**kernel-reader parse diagnostics**: notes 5700, lines 5700, malformed 0, bad_index 0, dup_index 0, bad_pred 0, bad_entity 20, kept 5680, truncated 0, run_env {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.80GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'edge': {'backend_kind': 'llama-server', 'url': 'http://127.0.0.1:8081', 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897', 'model_file': 'qwen3-1.7b-q4_k_m.gguf', 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'concurrency': 8, 'replay_only': False, 'calls_recorded_on': [{'recorded': None, 'calls': 296, 'replayed_here': 296}, {'recorded': {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.10GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'}, 'calls': 34, 'replayed_here': 34}, {'recorded': {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.80GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'}, 'calls': 99, 'replayed_here': 99}]}, 'kernel': {'backend_kind': 'llama-server', 'url': 'http://127.0.0.1:8081', 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897', 'model_file': 'qwen3-1.7b-q4_k_m.gguf', 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'concurrency': 8, 'replay_only': False, 'calls_recorded_on': [{'recorded': {'machine': {'platform': 'Linux-6.18.44-fc-v80-x86_64-with-glibc2.39', 'system': 'Linux', 'machine': 'x86_64', 'cpu': 'Intel(R) Xeon(R) Processor @ 2.80GHz', 'hw_model': '', 'n_cpus': 4, 'mem_gb': 15.7, 'python': '3.13.16'}, 'llama_cpp': 'b1-fc9ce6b', 'server_slots': 8, 'url_kind': 'loopback', 'model_sha256': 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'}, 'calls': 143, 'replayed_here': 0}]}, 'git': '9d4711f', 'prompt_version': 'edge-v2', 'harness': 'mycelic-live/2'}

## Systems, live vs simulated operators (same world)

| metric | A2_live sim | A2_live live |
|---|---|---|
| found_anywhere_in_register | 0.925 | 0.875 |
| found_supported | 0.925 | 0.875 |
| rare_supported | 0.833 | 0.833 |
| average_precision | 0.608 | 0.487 |
| discovery_precision | 0.252 | 0.185 |
| decoy_acceptance_all | 0.450 | 0.525 |
| decoy_D1_entity_coincidence | 0.200 | 0.200 |
| decoy_D2_temporal_scramble | 0.300 | 0.600 |
| decoy_D3_near_miss_entity | 0.500 | 0.500 |
| decoy_D5_stale_chain | 0.800 | 0.800 |
| independent_evidence_accuracy | 0.890 | 0.843 |
| lineage_accuracy | 0.819 | 0.770 |
| hallucination_rate | 0.014 | 0.027 |
| rare_signal_recall | 0.833 | 0.778 |
| n_reported | 147.000 | 184.000 |
| llm_calls | - | 143 |
| llm_prompt_tokens | - | 255116 |
| llm_completion_tokens | - | 57565 |
| llm_wall_s_est | - | 5276 |
| compute_units | 1.927e+05 | 1.996e+05 |
| wall_seconds | 1631 | 1674 |

_`llm_*`: real calls / tokens / estimated wall time of the live model calls; `compute_units` and `wall_seconds` are the simulator's meter (modelled). Kernel synthesis checks are simulated coins in both columns._

## Operator gap: edge extraction (small-7b tier vs live edge)

| quantity | small-7b nominal | sim small-7b (measured) | live edge (measured) | live - sim |
|---|---|---|---|---|
| claim emission rate | 0.701 (P(a note yields a claim)) | 0.700 | 0.958 | +0.258 |
| predicate error, causal notes, entity right | 0.224 (chain-neighbour slips only) | 0.094 | 0.300 | +0.206 |
| entity fidelity, predicate right | 0.843 (per (author, entity), same-stem partner) | 0.839 | 0.989 | +0.150 |
| predicate error, all claims | - | 0.228 | 0.165 | -0.063 |
| entity fidelity, all claims | - | 0.671 | 0.947 | +0.277 |
| spurious claims / note | 0.183 (1 - extract_precision) | 0.180 | 0.042 | -0.139 |
| polarity accuracy | 1.000 (exact) | 0.992 | 0.950 | -0.043 |
| exact-tuple precision | - | 0.648 | 0.788 | +0.140 |
| exact-tuple recall, all notes | - | 0.571 | 0.755 | +0.184 |
| exact-tuple recall, causal notes | - | 0.537 | 0.599 | +0.062 |
| exact-tuple recall, gold facet notes | - | 0.532 | 0.653 | +0.121 |
| exact-tuple recall, rare-pattern facet notes | - | 0.532 | 0.685 | +0.153 |
| echo signature pair precision | event id for 92% of claims | 0.999 | 1.000 | +0.001 |
| echo signature pair recall | event id for 92% of claims | 0.856 | 1.000 | +0.144 |

## Operator gap: A2 kernel reading (frontier-plus tier vs live kernel reader)

| quantity | frontier-plus nominal | sim frontier-plus (measured) | live kernel reader (measured) | live - sim |
|---|---|---|---|---|
| claim emission rate | 0.960 (P(a note yields a claim)) | 0.961 | 0.996 | +0.036 |
| predicate error, causal notes, entity right | 0.030 (chain-neighbour slips only) | 0.015 | 0.264 | +0.249 |
| entity fidelity, predicate right | 0.995 (per (author, entity), same-stem partner) | 0.996 | 0.989 | -0.006 |
| predicate error, all claims | - | 0.031 | 0.299 | +0.268 |
| entity fidelity, all claims | - | 0.979 | 0.942 | -0.037 |
| spurious claims / note | 0.015 (1 - extract_precision) | 0.016 | 0.051 | +0.034 |
| polarity accuracy | 1.000 (exact) | 0.999 | 0.881 | -0.119 |
| exact-tuple precision | - | 0.964 | 0.610 | -0.354 |
| exact-tuple recall, all notes | - | 0.942 | 0.608 | -0.334 |
| exact-tuple recall, causal notes | - | 0.942 | 0.608 | -0.334 |
| exact-tuple recall, gold facet notes | - | 0.947 | 0.600 | -0.347 |
| exact-tuple recall, rare-pattern facet notes | - | 0.937 | 0.604 | -0.333 |
| echo signature pair precision | event id for 92% of claims | 0.997 | 1.000 | +0.003 |
| echo signature pair recall | event id for 92% of claims | 0.805 | 1.000 | +0.195 |

## Projected wall time at the measured throughput (this machine)

* [A2 kernel reader, measured 47.9 s per chunk after 20 calls] rest of this run: 123 chunks -> 1.6 h; stage 2000: ~664 chunks -> 8.8 h; stage 10000: ~3321 chunks -> 44.2 h

_Run environment (this invocation): Intel(R) Xeon(R) Processor @ 2.80GHz (x86_64, 4 cpus, 15.7 GB), Linux-6.18.44-fc-v80-x86_64-with-glibc2.39; git 9d4711f; prompt edge-v2._

_edge: llama-server at http://127.0.0.1:8081 (loopback), model sha256 b139949c5bd74937…; calls recorded on:_
  * 296 calls (296 replayed here): unrecorded , llama.cpp None, None slots
  * 34 calls (34 replayed here): Intel(R) Xeon(R) Processor @ 2.10GHz Linux-6.18.44-fc-v80-x86_64-with-glibc2.39, llama.cpp b1-fc9ce6b, 8 slots
  * 99 calls (99 replayed here): Intel(R) Xeon(R) Processor @ 2.80GHz Linux-6.18.44-fc-v80-x86_64-with-glibc2.39, llama.cpp b1-fc9ce6b, 8 slots

_kernel: llama-server at http://127.0.0.1:8081 (loopback), model sha256 b139949c5bd74937…; calls recorded on:_
  * 143 calls (0 replayed here): Intel(R) Xeon(R) Processor @ 2.80GHz Linux-6.18.44-fc-v80-x86_64-with-glibc2.39, llama.cpp b1-fc9ce6b, 8 slots

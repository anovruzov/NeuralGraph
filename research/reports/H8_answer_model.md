# H8: stronger ANSWER model only (qwen3.6-35b-a3b answers, gemma-4-e4b reranks + judges)

**Verdict: DOES NOT WORK under the campaign judge (-7); MIXED on inspection.** Gold-in-context is identical and a judge-free substring score moves the other way (+6). The delta comes from Qwen obeying "output exactly: Not found" more literally, and from the Gemma judge crediting Gemma's vague paraphrases.

## Setup
Same 100 single_hop ids, RETRIEVAL_MODE=local_pairs, regex router, judge = local gemma-4-e4b (as in H1/H2/H7). Control ANSWER_MODEL=google/gemma-4-e4b; treatment ANSWER_MODEL=qwen/qwen3.6-35b-a3b (MoE 4-bit, ~3B active). Reranker, profile extractor and judge stay on Gemma in both runs.

## Results (same 100 ids)
| | control (gemma) | treatment (qwen) | delta |
|---|---|---|---|
| accuracy (gemma judge) | 78/100 | 71/100 | **-7** |
| gold in final context (>=60% gold tokens) | 71/100 | 71/100 | 0 (100/100 agree) |
| accuracy given gold in context | 63/71 = 88.7% | 56/71 = 78.9% | -7 |
| accuracy given gold absent | 15/29 | 15/29 | 0 |
| judge-free: >=60% gold tokens in answer | 45/100 | 51/100 | **+6** |
| judged correct but answer fails substring | 34 | 22 | -12 |
| "Not found" answers (with gold in ctx) | 10 (4) | 15 (11) | +5 |
| mean answer chars | 41 | 73 | +32 |
| answer latency mean / p90 | 501 / 705 ms | 913 / 1128 ms | +82% |
| rerank latency mean | 7351 ms | 7284 ms | unchanged |
| e2e latency mean | 7.92 s | 8.26 s | +4% |

wins=5, losses=12, both correct=66, both wrong=17. 7 of the 12 losses are Qwen abstaining ("Not found"), 9 of them with gold loosely in context. Final memory lists byte-identical for 87/100 ids (reranker drift), but gold-in-context agrees on all 100, so the comparison isolates the answer call.

## Examples
Wins: "What animal do both Nate and Joanna like?" (Turtles): gemma `pets`, qwen `turtles`. "What does Melanie do with her family on hikes?" (roast marshmallows, tell stories): gemma `hiking, exploring nature`, qwen `roasted marshmallows, told stories`. "What did Nate give Joanna?" (stuffed toy pup): gemma `Not found`, qwen `stuffed animal`.

Losses: "Where did Caroline move from?" (Sweden, in ctx): gemma `home country` (judged correct), qwen `Not found`. "What book did Melanie read?" (Becoming Nicole, in ctx): gemma right, qwen `Not found`. "What area was hit by a flood?" (West County, in ctx): gemma `West County`, qwen `John's old area`.

## Interpretation
1. Retrieval decides: both models score 15/29 on the gold-absent questions.
2. Qwen is stricter and more literal: 11 abstentions with gold present (Gemma 4) and longer lists, so its answers contain the gold more often (+6 substring) yet get judged wrong more often. Prompt-following, not capability: a bigger answer model needs its own prompt (soften "Not found", cap list length).
3. Same-model judge confound: Gemma judged 34 of its own non-substring answers correct vs 22 of Qwen's ("home country" for Sweden). N5 should rescore both H8 JSONs with a strict judge; the sign may flip.

## Load / memory
Verification curl: HTTP 200 in 13.8 s cold (JIT load + answer), content `Biscuit`, `reasoning_tokens=0`: `reasoning_effort: none` disables Qwen-3.6 thinking, no /no_think needed. Warm-up 2.5 s. Gemma, nomic and Qwen stayed loaded together before, during and after the run (nothing evicted or unloaded); no memory pressure; rerank latency unchanged, so Qwen residency did not slow Gemma.

## Code changes (worktree agent-aea3a694c25e1704f, uncommitted)
- NeuralGraph/llm_backend.py:26 `ANSWER_MODEL = os.environ.get("ANSWER_MODEL", LLM_MODEL)` (+docstring :14).
- NeuralGraph/answering.py:17 `AnsweringConfig.answer_model` defaults to `llm_backend.ANSWER_MODEL`.
- demo/runner.py:65,69 `ANSWERING_CONFIG` uses `ANSWER_MODEL`; :1044 summary prints answer/rerank/judge models. `RERANKER_MODEL` (:302) and the local judge stay on `LLM_MODEL`.

## Cost
Lock held 13:46-14:17 (31 min): control 14.2 min, treatment 14.7 min, 14 s model load. Files here: H8_control.json, H8_treatment.json, run_H8_*.log, analyze_h8*.py.

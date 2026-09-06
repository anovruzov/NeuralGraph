# H1: batched listwise reranking vs one-call-per-candidate pointwise

Setup: single_hop, 100 q (convs 1-4, same ids in all runs), local_pairs, gemma-4-e4b reranks/answers/judges (local judge as in Phase 0). Rerank window 80.

| run | accuracy | rerank ms mean/med | e2e ms | gold-in-ctx (runner rule) | gold-in-ctx (exact) | acc given gold in ctx | LLM calls/q |
|---|---|---|---|---|---|---|---|
| H1_control (pointwise 0-3) | **78/100** | 7391 / 7376 | 7948 | 71% | 16% | 88.7% (71) | 80 |
| H1_treatment (listwise, 8 batches x10, 0-10) | 74/100 | **3258 / 3255** | **3816** | 71% | 17% | 84.5% (71) | 8 |
| H1_treatment_pass2 (+ global re-score of per-batch top-3) | 72/100 | 5845 / 5880 | 6387 | 71% | 17% | 83.1% (71) | 9 |

"runner rule" = `check_gold_in_memories_substring` (any gold part in any final-context memory); "exact" = whole gold string. Final context = `retrieved_memories` (mean 25.8 entries).

Flips vs control: listwise 4 W / 8 L; pass2 3 W / 9 L. Ordering really changed (context Jaccard vs control 0.48; same top-1 only 30/100).

## Verdict: MIXED — latency WORKS (-56%), accuracy DOES NOT (-4 pts)
- Latency: rerank 7.4 s -> 3.3 s, e2e 7.9 s -> 3.8 s. Rerank was the dominant cost; this is real.
- Accuracy: 78 -> 74 (CI ~+/-8 at n=100, so not a clean loss, but direction negative). Accuracy given gold in context also drops (88.7 -> 84.5), the one thing a reranker should improve.
- Gold-in-context is identical (71%) in all runs: retrieval, not the reranker, decides whether gold reaches the answer model; the reranker only moves position, and the answerer is fairly position-insensitive over ~26 memories.
- Second pass does not help: -2 more points, +2.6 s.
- Premise confirmed (pointwise ties leave order to charge), but that order was slightly better. Listwise 0-10 scores are sparse (most 0, one or two at 5-10), so a single noisy judgment outweighs the charge.
- Parsing: 0 failures / timeouts in 800 batch calls (all JSON, some fenced).

Plain statement: listwise wins on latency but not on accuracy.

## Examples
- WIN: "What subject have Caroline and Melanie both painted?" gold Sunsets. ctl "nature" (gold absent) -> listwise "painting, nature" (gold present; judge leniency).
- WIN: "Who gave Maria's family money when she was younger..." gold Her aunt. ctl "4 August 2023" -> listwise "Auntie".
- WIN: "What states has Maria vacationed at?" gold Oregon, Florida. ctl adds "Spain" -> listwise "Florida, California, Oregon".
- LOSS: "What did Melanie paint recently?" gold sunset. ctl "landscapes, horse, sunset painting" -> listwise "horse painting, landscape, still life" (gold in ctx both times, pushed lower).
- LOSS: "What musical artists/bands has Melanie seen?" ctl "Matt Patterson" -> listwise "Not found".
- LOSS: "When did Melanie go on a hike after the roadtrip?" ctl found the 20 Oct "yesterday" turn -> listwise "No memory".

## Recommendation
Keep `RERANK_MODE=pointwise` default; use `listwise` where latency matters (halves e2e for ~4 pts). Untested: listwise as pruner 80 -> 30, then pointwise.

## Code (worktree /Users/nurmanmahammadov/Desktop/NeuralGraph/.claude/worktrees/agent-abcc79efb7d56c735, uncommitted)
- NeuralGraph/reranker.py:629 `_parse_listwise_scores` (JSON -> fenced -> regex `"k": s` -> default 3); :673 `_listwise_prompt` ("[k] (speaker) text[:300]", 0-10 rubric); :692 `_listwise_score_batch` (llm_generate, temp 0, max_tokens 120, timeout 30 s, RERANK_DEBUG=1 prints raw); :719 `rerank_candidates_listwise(query, candidates, limit, batch_size=10, ..., second_pass=False)` — asyncio.gather over batches, sort (score, charge), returns (node, charge + score*0.05).
- demo/runner.py:29 import; :303-306 `RERANK_MODE` (default pointwise), `RERANK_LISTWISE_BATCH`, `RERANK_LISTWISE_PASS2`; :841-850 dispatch.

## Cost
Local LM Studio, $0. Wall ~50 min for 3x100 q (control 13, listwise 7, pass2 11 min) + 40 min queue wait. Files next to this report: H1_control.json, H1_treatment.json, H1_treatment_pass2.json, run_H1_*.log, analyze_h1.py, analyze_h1b.py. LM Studio lock released at 09:10.

# H2: does the answer model see too few memories? — DOES NOT WORK

**Verdict: DOES NOT WORK.** 15/30 (control) 78.0% -> 30/30 76.0% (-2) -> 50/50 69.0% (-9). Gold is essentially never beyond rank 15 after reranking, and gemma-4-e4b gets more distracted as context grows (accuracy when gold IS in context: 88.7% -> 78.1%). Keep NUM_MEMORIES=15, NUM_MEMORIES_LIST=30.

## Setup
ONLY_CAT=single_hop MAX_QUESTIONS=100 RETRIEVAL_MODE=local_pairs, same 100 ids, gemma-4-e4b answer+rerank+local judge, temp 0. 72/100 of these questions are routed LIST/AGGREGATION and already got 30 memories in control; only 28 get 15. Treatments therefore set both knobs (30/30, 50/50).

## Results (same 100 ids)
| run (plain/list) | acc | gold in ctx | acc \| gold in | acc \| gold out | ctx n | answer ms | rerank ms | e2e ms |
|---|---|---|---|---|---|---|---|---|
| control 15/30 | **78.0%** | 71.0% | 88.7% | 51.7% | 25.8 | 486 | 7372 | 7937 |
| 30/30 | 76.0% | 71.0% | 87.3% | 48.3% | 30.0 | 506 | 7364 | 7953 |
| 50/50 | 69.0% | 73.0% | 78.1% | 44.4% | 50.0 | 593 | 7332 | 8006 |

Noise floor: control vs the identical-config main-repo local_pairs run (77.0%) differs on 1/100 questions. -2 is at the noise edge; -9 is real.
Per subset (control routing): plain 28 q 67.9 -> 60.7 -> 53.6%; list/agg 72 q 81.9 -> 81.9 -> 75.0%. Gold-in-context unchanged at 30, +1 question each at 50.

Rank of first gold hit after rerank (control): 1-5: 52, 6-15: 15, 16-30: 4, none: 29. Widening 15->30 on the plain subset added zero gold hits; 30->50 added 2 (both still wrong). Post-rerank gold rank is bimodal (top 15 or absent), so context size cannot buy recall; that headroom belongs to retrieval/rerank (H1/H3/H9).

Head-to-head: 30/30 vs control +0/-2; 50/50 +1/-10. Answers changed: 14/100 at 30, 49/100 at 50.

## Wins / losses (50/50 vs control)
Win: Q22 "subject Caroline and Melanie both painted?" gold Sunsets: `nature` -> `nature, sunsets` (gold entered at rank 31-50).
Losses (gold in context in all runs): Q4 "Where did Caroline move from?" gold Sweden: `home country` -> `Not found`. Q13 "What did Melanie paint recently?" gold sunset: `landscapes, horse painting, sunset painting` -> `Not found`. Q29 book suggestion: `Becoming Nicole by Amy Ellis Nutt` -> `Not found`. Q67 flood area: `West County` -> `My old area`.
Pattern: at 50 memories the model abstains or picks a vaguer neighbouring memory although the exact gold message is in the prompt. Abstain-style answers 10 -> 10 -> 12, all new abstains on gold-in-context questions. 30/30 losses: Q28 (dropped "poetry reading" from a list), Q31 (temporal, gold absent in both).

## num_predict=120 truncation check
No truncation. Longest answer in all 300 rows is 32 words (~45 tokens) vs the 120-token cap; 0 answers match a cut-off heuristic (dangling connector/comma or >=85 words). 98-99/100 answers lack terminal punctuation, but that is the STRICT prompt style (`Sara, Kyle`), not truncation. Phase 0 flat (282 q): max 44 words, 0 cut.

## Code change (worktree agent-a73febabef3562263, not committed, not copied to main)
- `demo/runner.py:94-99`: env `NUM_MEMORIES` (default 15), `NUM_MEMORIES_LIST` (default 30).
- `demo/runner.py:838`: `num_memories_needed = NUM_MEMORIES_LIST if query_mode in {"AGGREGATION","LIST"} else NUM_MEMORIES` (was hard-coded 30/15). Defaults reproduce old behaviour.

## Cost
3 x 100 q, ~14 min each (42 min LM Studio + ~35 min waiting for H1's lock). Answer latency 486 -> 506 -> 593 ms, dwarfed by ~7.3 s rerank; e2e +1%.

## Takeaway
Context size is not a lever. Remaining single_hop headroom: (a) retrieval recall (29% of golds never reach the final context), (b) answer faithfulness when gold is present (88.7% at 15, worse wider). A smaller context (10) is a cheap test worth running; wider is not. Files: `H2_*.json`, `h2_*.log`, `run_h2.sh`, `analyze_h2*.py` in overnight/.

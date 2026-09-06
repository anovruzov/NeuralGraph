# N1: does a SMALLER answer context (10 or 5 memories) beat 15/30? — DOES NOT WORK

**Verdict: DOES NOT WORK.** 15/30 (control) 78.0% -> 10/15 71.0% (-7) -> 10/10 71.0% (-7) -> 5/10 69.0% (-9). With H2 (30 -> 76, 50 -> 69) the sweep 5..50 peaks at 15/30. The distraction hypothesis is refuted directly: on the 60 questions whose gold was already in the top 10 (present in every treatment) accuracy went 91.7% -> 83.3 / 88.3 / 85.0%, never up. Keep NUM_MEMORIES=15, NUM_MEMORIES_LIST=30.

## Setup
ONLY_CAT=single_hop MAX_QUESTIONS=100 RETRIEVAL_MODE=local_pairs, same 100 ids as H1/H2/H7, gemma-4-e4b answer + pointwise rerank + local judge, temp 0. 72/100 routed LIST/AGGREGATION, 28 plain. Control reproduces H2 control exactly (78/100, identical gold-in-context, 0 correctness flips).

## Results (same 100 ids)
| run (plain/list) | acc | gold in ctx | acc \| gold in | acc \| gold out | ctx n | answer ms | e2e ms | wins/losses |
|---|---|---|---|---|---|---|---|---|
| control 15/30 | **78.0%** | 71% | 88.7% (71) | 51.7% (29) | 25.8 | 479 | 7955 | - |
| T1 10/15 | 71.0% | 65% | 83.1% (65) | 48.6% (35) | 13.6 | 250 | 7712 | +1 / -8 |
| T2 10/10 | 71.0% | 60% | 88.3% (60) | 45.0% (40) | 10.0 | 190 | 7628 | +2 / -9 |
| T3 5/10 | 69.0% | 57% | 87.7% (57) | 44.2% (43) | 8.6 | 180 | 7603 | +2 / -11 |

Subsets: plain 28 q 67.9 -> 64.3 -> 64.3 -> 57.1%; list/agg 72 q 81.9 -> 73.6 -> 73.6 -> 73.6%. Abstains 10 -> 11 in all treatments.
Answer latency halves (479 -> 190 ms) but is 2% of e2e; rerank (7.4 s, same 80-candidate window) dominates, so e2e improves only 3-4%.

Accuracy by CONTROL post-rerank gold rank (n): 1-5 (53) 92.5 -> 86.8 / 90.6 / 90.6%; 6-10 (7) 85.7 -> 57.1 / 71.4 / 42.9%; 11-15 (7) 100 -> 85.7 / 42.9 / 42.9%; 16-30 (4) 25% flat; absent (29) 51.7 -> 48.3%.

Two loss channels of similar size: (a) recall — truncation removes the first gold hit for 6 / 11 / 14 questions (T1/T2/T3); (b) corroboration — losses with gold STILL in context (T1: 6 of 8 losses; T2: 3 of 9; T3: 3 of 11). 69/100 golds are multi-part lists spread over several memories; control covers all parts for 28 questions, T2 for 25. Ranks 11-30 carry the second mention, and gemma drops an item it saw only once.

## Wins / losses (T2 10/10 vs control)
Wins: Q63 "states Maria vacationed at" gold Oregon, Florida: `Florida, California, Oregon, Spain` -> `Florida, California, Oregon`. Q88 "Nate gave Joanna" gold stuffed toy pup: `Not found` -> `stuffed animal, good vibes` (gold absent in both).
Losses: Q1 "What did Caroline research?" gold adoption agencies, rank 1 in both runs: `adoption agencies, counseling and mental health` -> `counseling, mental health` — the corroborating memory ("find an adoption agency or lawyer") sat at rank 21. Q19 pottery gold bowls, cup (rank 11 -> cut): `pots, cup, plate, bowl` -> `pots, clay`. Q25 bands gold Summer Sounds, Matt Patterson (rank 6 kept): `Matt Patterson` -> `Not found`. T1-only losses with gold present: Q4 Sweden rank 8 `home country` -> `Not found`; Q13 sunset rank 7 -> `Not found`; Q66 `Sara, Kyle` -> `Sara` (Sara's mention beyond rank 15). T3 adds Q29 "Becoming Nicole" (rank 10, cut at 5) and Q52 yoga partner `Rob` -> `Maria`.

## Code change (worktree agent-a1dfeecf63680d4ce, not committed, not copied to main)
- `demo/runner.py:101-104`: env `NUM_MEMORIES` (default 15), `NUM_MEMORIES_LIST` (default 30) — the H2 knob.
- `demo/runner.py:837`: `num_memories_needed = NUM_MEMORIES_LIST if query_mode in {"AGGREGATION","LIST"} else NUM_MEMORIES`. Defaults reproduce old behaviour.

## Cost
Smoke + 4 x 100 q, ~14 min each = 57 min LM Studio (11:48-12:44), ~45 min waiting for H5's lock.

## Takeaway
Context size is now fully characterised: 5 -> 69, 10 -> 71, 15/30 -> 78, 30 -> 76, 50 -> 69. Fewer memories does not reduce distraction for gemma-4-e4b; it removes second mentions that list answers need and cuts the 11 golds at ranks 11-30. Remaining single_hop headroom is retrieval (29% of golds never reach the context) and reranker placement (lift ranks 11-30 into the top 10). Files: `N1_control.json`, `N1_T1.json`, `N1_T2.json`, `N1_T3.json`, `run_N1_*.log`, `run_n1.sh`, `analyze_n1*.py` in overnight/.

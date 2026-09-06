# N4: show the reranker / answerer the previous message ("pair" context)

Setup: single_hop, 100 q (convs 1-4, same ids as H1/H2/N1), RETRIEVAL_MODE=local_pairs, gemma-4-e4b reranks (pointwise 0-3, window 80) / answers / judges. Control reproduces H1_control exactly (78/100, 7.35 s rerank).

| run | accuracy | rerank ms mean/med | e2e ms | gold-in-ctx (runner rule) | gold exact | acc given gold | flips |
|---|---|---|---|---|---|---|---|
| N4_control (single/single) | **78** | **7349 / 7346** | **7782** | 71% | 16% | 88.7% (71) | - |
| T1 RERANK_CONTEXT=pair | 71 | 8479 / 8467 | 9080 | 69% | 14% | 82.6% (69) | 1 W / 8 L |
| T2 ANSWER_CONTEXT=pair | 75 | 7989 / 7980* | 8642 | 73% | 17% | 83.6% (73) | 3 W / 6 L |
| T3 both | 66 | 8197 / 7982 | 8804 | 70% | 15% | 78.6% (70) | 1 W / 13 L |

Runner rule = any comma/and-split gold part in any final-context memory (prev lines included for T2/T3); exact = whole gold string. Context = 25.8 memories/q; T2/T3 add 20.2 / 18.7 prev lines. *T2 leaves the reranker untouched, so its +0.6 s is LM Studio noise; T1's real cost is about +0.5-1.1 s (80 longer prompts).

## Verdict: DOES NOT WORK (T1 -7, T2 -3, T3 -12; losses add up)

- **T1 (reranker sees "Previous: [speaker] text")**: ordering changed a lot (same top-1 only 52/100, context Jaccard 0.62) but for the worse: gold-in-ctx 71 -> 69, acc-given-gold 88.7 -> 82.6. Mechanism (n4_diag.py): the previous line dilutes the candidate. "What did Caroline research?" gold "Researching adoption agencies..." scored 3 alone (boosted charge 1.312, rank 1) but 1 with Melanie's prompt above it (0.912, rank 4); the answer then followed three "counseling / mental health" memories. The poster case ("Where did Caroline move from 4 years ago?" gold Sweden) sat at rank 8 in both runs; its flip is answer noise. Mean boosted charge is unchanged (0.971 vs 0.979): reshuffling, not a shift.
- **T2 (answer prompt shows the pair)**: memory set identical to control (Jaccard 1.00, same top-1 98/100); only the prev lines differ, yet accuracy drops 78 -> 75 and acc-given-gold 88.7 -> 83.6. Same effect as H2: ~20 extra utterances distract gemma ("Becoming Nicole by Amy Ellis Nutt" -> "Not found" with gold present). The 3 wins are judge-lenient paraphrases.
- **T3**: the two losses add (13 L); no interaction rescues either.

Why H3 did not transfer: pair nodes help *retrieval* because the reply's embedding gains the question's topic. The reranker already sees the question; a second text makes it credit or penalise the candidate for its neighbour. The answerer is already 88.7% correct when gold is present; more text per memory only lowers that.

Plain statement: keep both flags at `single`. The pair idea belongs in retrieval only.

## Examples

- T1 LOSS: "What did Caroline research?" gold Adoption agencies. ctl "adoption agencies, counseling and mental health" -> "counseling, mental health" (gold rank 1 -> 4).
- T1 LOSS: "What types of pottery have Melanie and her kids made?" gold bowls, cup. ctl "pots, clay, cup, plate, bowl" -> "pots, clay".
- T1 LOSS: "What type of volunteering have John and Maria both done?" gold homeless shelter. ctl "volunteering, helping others" -> "volunteering".
- T1 WIN (the only one; also T3's only win): "What states has Maria vacationed at?" gold Oregon, Florida. ctl adds "Spain" -> "Florida, California, Oregon".
- T2 WIN: "What subject have Caroline and Melanie both painted?" gold Sunsets. ctl "nature" -> "nature, sunset".
- T2 WIN: "What is something Nate gave to Joanna that brings her joy?" gold stuffed toy pup. ctl "Not found" -> "stuffed animal, encouragement".
- T2 LOSS: "What book did Melanie read from Caroline's suggestion?" gold Becoming Nicole. ctl correct -> "Not found" (gold in context both times).
- T2 LOSS: "What items has Melanie bought?" gold Figurines, shoes. ctl "necklace, figurines, shoes" -> "necklace, shoes".

## Code (worktree .claude/worktrees/agent-a4b9c3ab88f843022, uncommitted; defaults keep current behaviour)

- NeuralGraph/reranker.py:179,328,406,488 `score_candidate(..., context: str = "")`; :342 LLMReranker puts the context line above "Memory from [speaker]"; :589 `rerank_candidates_parallel(..., context_by_id=None)`; :615-619 builds `Previous: [speaker] text[:200]` from `context_by_id[node.node_id]`.
- demo/runner.py:114-115 `RERANK_CONTEXT` / `ANSWER_CONTEXT`; :715 `prev_by_id` (node_id -> preceding node, from ordered `all_nodes`); :852 passes `context_by_id`; :872-876 `prev_line` (skipped when the previous message is itself selected); :890,:895 prepended in both context formats; :903-906 `prev_speaker`/`prev_text` saved in `retrieved_memories`.

## Cost

Local LM Studio, $0. Lock held 12:44:50-13:46:18 (4 x ~15 min); queued 11:00-12:44 behind H5 and N1. Files here: N4_control.json, N4_T1_rerank_pair.json, N4_T2_answer_pair.json, N4_T3_both.json, run_N4_*.log, run_n4.sh, analyze_n4.py, n4_diag.py, n4_mock_test.py.

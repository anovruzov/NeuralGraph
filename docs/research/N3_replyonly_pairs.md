# N3: reply-only unpairing of pair nodes (retrieval-only, 0 LLM calls)

All 10 LoCoMo conversations; local routing when exactly one speaker is named (1062/1219), pooled fallback otherwise; window-2 pair nodes from cache; substring recall@10 / @50 / @all. 137 s. Run `MODE=replyonly demo/two_agent_eval.py`; rows `scratchpad/overnight/n3_rows.json`, log `n3_full.log`, analysis `analyze_n3.py` / `n3_lost.py`.

## Results (recall@10 / @50 / @all, %)

| # | variant | single_hop (282) | multi_hop (841) | open_domain (96) | window |
|---|---|---|---|---|---|
| 0 | tess_local | 46.8 / 62.8 / 62.8 | 50.3 / 63.3 / 63.3 | 15.6 / 19.8 / 19.8 | 50 |
| 1 | **tess_local+pairs (control, both members)** | 46.8 / 62.8 / **69.5** | 50.3 / 63.3 / **68.4** | 15.6 / 19.8 / **24.0** | 80 |
| - | embed_local+pairs, both members (H9) | 41.1 / 59.2 / 59.2 | 53.7 / 64.0 / 64.0 | 14.6 / 20.8 / 20.8 | 50 |
| 2 | embed_local+pairs_replyonly | 45.7 / 62.1 / 62.1 | **55.4** / 64.2 / 64.2 | 12.5 / 18.8 / 18.8 | 50 |
| 3 | tess_local+pairs_replyonly | 46.8 / 62.8 / 68.4 | 50.3 / 63.3 / 67.1 | 15.6 / 19.8 / 21.9 | 80 |
| 4 | pairs_only_local_replyonly | 45.4 / 62.1 / 62.1 | **55.4** / 64.2 / 64.2 | 12.5 / 18.8 / 18.8 | 50 |
| 5 | tess_local + 30 from variant 4 | 46.8 / 62.8 / 67.7 | 50.3 / 63.3 / 67.1 | 15.6 / 19.8 / 21.9 | 75 |
| 6 | variant 2 base + 30 tess_local | 45.7 / 62.1 / 67.0 | 55.4 / 64.2 / 67.1 | 12.5 / 18.8 / 20.8 | 75 |

Control reproduces H9 exactly. single_hop rank of the first gold-bearing node (found questions only): control median 5.0 (196 found, mean 13.7); variant 3 median 5.0 (193, mean 12.9); variant 4 median 4.0 (175, mean 8.9). rank<=10 counts are 132 / 132 / 128, so the head the reranker sees is identical for 1 and 3; variant 4's lower median comes only from finding fewer questions.

## Verdicts

- **2 / 4 (reply-only as base): partial fix, not a win.** Recovers most of the base-position loss (single_hop @10 41.1 -> 45.7, still -1.1 below tess_local) and gives the best multi_hop @10 seen (55.4, +5.1). Variants 2 and 4 are the same retriever (identical hits on 1218/1219 questions): the pair containing message *i* as reply outranks bare message *i* essentially always, so message nodes are dead weight once pairs are indexed. But @all is 62.1 / 64.2 vs the control's 69.5 / 68.4, and open_domain falls to 12.5.
- **3 / 5 (reply-only back-fill): WORSE, -1.1 / -1.3 / -2.1 @all** (+2/-5, +5/-16, +0/-2 questions). 5 equals 3 in outcome.
- **6: WORSE** than control at @all (67.0 / 67.1 / 20.8) and at single_hop @10; only multi_hop @10 improves.

Why it fails: of the 23 questions the control finds and variant 3 loses, **14 have the gold substring only in the OTHER agent's message** (conv3 "What is Nate's favorite movie trilogy?" -> Joanna says "Lord of the Rings"; conv5 "How many dogs does Andrew have?" -> Audrey says "3"), 5 in both agents' messages, 1 only in the named agent's replies, 3 pooled. Under local routing the both-member `unpair` is the *only* channel through which the un-named agent's utterance enters the candidate list, and the prompt of a top pair is exactly the message that states the named agent's fact. The head "dilution" and the tail cross-speaker recovery are one mechanism; behind a Tesseract top-50 the leak is free at @10/@50 and worth +1 to +2 @all (gold only in the other agent for 29, in both for 337 of the 1062 one-named questions).

## Recommendation

**Ship the control unchanged: `tess_local+pairs` with both-member unpairing.** No change to `demo/runner.py` mode `local_pairs`: keep `unpair` at `demo/runner.py:457-465` (reply first, then prompt) and the query-time block at `demo/runner.py:752-759` (`unpair(cosine_topk(pool, query_emb, 2 * TOP_K), node_by_id, seen)`, 30 appended). Do not adopt reply-only mapping in the shipped path.

Only follow-up worth a run: a 110-candidate list (tess_local + 30 reply-only + 30 prompt members) to test whether variant 2's multi_hop head gain and the control's tail can coexist, at higher rerank cost.

## Code (worktree agent-a411b332f17790fab, uncommitted, `demo/two_agent_eval.py`)

`unpair_replyonly` :401 (pair hit -> `pair_members[0]` only); `first_gold_rank` :414; `replyonly_main` :425, shared pair ranking :457, variants 1-6 :459-470; `MODE=replyonly` dispatch in `__main__`. No new embeddings.

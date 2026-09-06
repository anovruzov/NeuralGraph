# H9: LoCoMo as a two-agent memory benchmark (retrieval-only, 0 LLM calls)

All 10 conversations, repo category mapping, recall@10 / @50 by gold-substring. `tess_pooled` reproduces the baseline exactly. 8 min runtime. Per-question rows: `scratchpad/overnight/h9_rows.json`.

## H9a: per-agent memory split

| variant | single_hop (n=282) | multi_hop (n=841) | open_domain (n=96) |
|---|---|---|---|
| tess_pooled (control) | 39.4 / 61.7 | 46.8 / 61.4 | 13.5 / 20.8 |
| tess_local (route to named agent) | **46.8 / 62.8** | **50.3 / 63.3** | 15.6 / 19.8 |
| tess_local_oracle (route to evidence speaker) | 46.5 / 61.7 | 51.0 / 64.0 | 15.6 / 19.8 |
| tess_federated (25+25, merge by charge) | 36.9 / 60.3 | 42.4 / 59.9 | 11.5 / 20.8 |
| tess_federated_rr (25+25, round-robin) | 40.4 / 60.3 | 45.9 / 59.9 | 12.5 / 20.8 |
| embed_pooled | 25.9 / 52.1 | 35.1 / 54.8 | 13.5 / 19.8 |
| embed_local | **46.8 / 63.8** | **52.8 / 64.4** | 13.5 / 17.7 |
| embed_local_oracle | 46.8 / 62.4 | 54.0 / 65.2 | 13.5 / 18.8 |
| embed_federated | 25.9 / 62.1 | 35.1 / 63.7 | 13.5 / 17.7 |

Routing: 1062/1219 questions name exactly one speaker (244 single_hop, 743 multi_hop); 139 name both, 18 neither (fall back to pooled). **Gold spoken by the OTHER agent than the one named: 0.0% single_hop, 4.2% multi_hop, 0.0% open_domain (2.9% overall by LoCoMo evidence ids; 4.0% by substring).** Local memory is essentially never unanswerable on LoCoMo. Local beats pooled at r@10 in 10/10 conversations (embedding) and 9/10 (tesseract; conv5 56.5 -> 54.3).

Interpretation: routing to the named agent's own store is the largest retrieval gain found so far (+7.4 single_hop / +3.5 multi_hop r@10 for tesseract; +21 / +18 for pure embedding), and name-routing equals oracle routing because facts about X are almost always uttered by X. Federating by charge is *worse* than pooling (per-store charge normalisation is not comparable across agents); round-robin only recovers pooled. Caveat: a local store is half the size, so r@50 covers ~30% of it vs 15% pooled; r@10 is the fair comparison and still shows the gain. embed_local r@50 (63.8) beats the best prior variant (61.7).

## H9b: speaker-swap probe (single_hop naming one speaker, n=244)

| variant | orig r@10 | swap r@10 | gap@10 | orig r@50 | swap r@50 | gap@50 | identity blindness (swap hits top-10 given orig hit) |
|---|---|---|---|---|---|---|---|
| embed | 26.6 | **34.8** | **-8.2** | 54.1 | 61.5 | -7.4 | 78.5% (51/65) |
| tesseract | 42.6 | 32.0 | 10.7 | 65.2 | 53.7 | 11.5 | 57.7% (60/104) |
| tesseract+speaker (x1.5) | 48.8 | 26.2 | **22.5** | 65.2 | 53.7 | 11.5 | 45.4% (54/119) |

Why swapping *raises* embedding recall: a speaker's own name appears in 0.1% of their messages, the other's name in 33.4% (5882 messages). A name in the query is an embedding-level pointer to the OTHER agent's utterances, which do not carry the gold. tess+speaker gap is positive in 10/10 conversations; embed gap <= 0 in 7/10.

Interpretation: the embedding does not know who spoke; only metadata does. Lexical identity cues in dialogue memory are anti-correlated with authorship, so identity must come from the speaker field (speaker boost gives the largest sensitivity; local routing is its limiting case). Even boosted, 45% identity-blind, so hard speaker gating has headroom.

## Code (worktree agent-adbcc163c337592e3, uncommitted)

New `demo/two_agent_eval.py`; reuses `demo/retrieval_eval.py` `ingest`/`cosine_topk`/`cached_embeddings` unchanged:
- `named_speakers` :41 (same rule as `tesseract+speaker`), `swap_name` :47 (case-preserving whole-word regex)
- `speaker_boost` :73, `merge_by_charge` :79, `merge_round_robin` :88
- `build_agent_store` :98 (re-saves shared node objects into a fresh `InMemoryNeuralGraphStorage`, no edges)
- `evidence_speakers` :111 (evidence dia_ids -> speaker), `substring_gold_speakers` :119
- H9a loop `main` :158-190, H9b loop :192-222, `report` :231
- Swapped-question embeddings cached as `emb_cache/conv{idx}_questions_swapped.json` (244 vectors, one batch call).

Main's uncommitted modules were synced into the worktree. Note: committed `demo/runner.py` at HEAD hardcodes an OpenAI API key (line 72); it is in git history and should be rotated.

## Figure

Two panels. (a) Memory topology: x = scope, pooled -> federated -> local (name-routed) -> local (oracle); y = recall@10; lines for Tesseract and pure embedding, solid single_hop / dashed multi_hop. Embedding rises 26 -> 47, Tesseract 39 -> 47, converging at local: per-agent memory erases the elaborate retriever's advantage. (b) Identity sensitivity: paired bars (original vs name-swapped question), recall@10 for embed / tesseract / tesseract+speaker, gaps -8.2 / 10.7 / 22.5 annotated, footnote "own name in 0.1% of own messages, other's in 33%": embeddings point at the wrong agent; only speaker metadata restores identity.

## Combined stack (per-agent routing + H3 pair nodes)

All 10 conversations, recall@10 / @50 / @all. "Local" = the named agent's message nodes and the pair nodes whose replier is that agent; questions naming 0 or 2 speakers (157/1219) fall back to the pooled version of the same variant. Pair code ported from worktree agent-a96e7782c6d2db9a2 (`build_pair_nodes`, `unpair`, window 2, cached `conv{idx}_pairs2`). Run: `MODE=combined`, 2 min; rows in `scratchpad/overnight/h9_combined_rows.json`.

| variant | single_hop (n=282) | multi_hop (n=841) | open_domain (n=96) | window |
|---|---|---|---|---|
| embed_local (control) | 46.8 / 63.8 / 63.8 | 52.8 / 64.4 / 64.4 | 13.5 / 17.7 / 17.7 | 50 |
| embed_local+pairs | 41.1 / 59.2 / 59.2 | **53.7** / 64.0 / 64.0 | 14.6 / 20.8 / 20.8 | 50 |
| tess_local (= tess_local+speaker) | 46.8 / 62.8 / 62.8 | 50.3 / 63.3 / 63.3 | 15.6 / 19.8 / 19.8 | 50 |
| **tess_local+pairs** (tess top-50 + 30 pair back-fill) | 46.8 / 62.8 / **69.5** | 50.3 / 63.3 / 68.4 | 15.6 / 19.8 / **24.0** | 80 |
| stack: embed_local+pairs top-50 + 30 tess_local | 41.1 / 59.2 / 68.1 | 53.7 / 64.0 / **68.6** | 14.6 / 20.8 / 24.0 | 80 |
| stack: tess_local top-50 + 30 embed_local+pairs | 46.8 / 62.8 / 69.1 | 50.3 / 63.3 / 68.4 | 15.6 / 19.8 / 24.0 | 80 |
| control: tess_local + 30 plain embed_local back-fill | 46.8 / 62.8 / 66.0 | 50.3 / 63.3 / 65.4 | 15.6 / 19.8 / 19.8 | 80 |
| control: embed_local top-80 | 46.8 / 63.8 / 66.7 | 52.8 / 64.4 / 66.0 | 13.5 / 17.7 / 21.9 | 80 |

Reading: pair nodes are a good *back-fill* but a bad *base*. Inside the top-50 they dilute single_hop (46.8 -> 41.1 @10: unpairing pulls the other agent's prompt message in alongside each reply and spends rank slots on it), while as the 30-slot back-fill behind tesseract they lift recall@all by +6.7 single_hop / +5.1 multi_hop / +4.2 open_domain over tess_local, and by +3.5 / +3.0 / +4.2 over a plain embedding back-fill of the same size, so the gain is the pair representation, not the wider window. Base order matters only at @10: tesseract-first keeps the single_hop head, embed-pairs-first wins multi_hop @10 by 3.4 but loses single_hop @10 by 5.7. Prior best (tesseract+speaker+embed_union, pooled) was 65.2 / 65.6 @all.

**Recommendation: ship `tess_local+pairs`** — route the question to the named agent's store (pooled fallback for 0/2 names), Tesseract top-50, then append 30 pair-node hits from that agent's pairs; it matches the best head (@10/@50) and gives the best rerank window (@all 69.5 / 68.4 / 24.0) at the same 80-candidate cost as the existing embed_union. Code: `demo/two_agent_eval.py` `build_pair_nodes` / `unpair` / `append_extra` / `combined_main`.

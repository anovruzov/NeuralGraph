# NeuralGraph overnight campaign: final report

Date: 2026-09-05, 06:15 to 15:50 CDT. 13 experiments, 12 subagents, one LM Studio server (gemma-4-e4b, nomic-embed v1.5, qwen3.6-35b-a3b as independent judge). Every number below is leakage-free: the gold-answer gate and gold-category routing found in the audit were removed before any run.

## 1. Headline numbers

Best configuration = per-agent memory routing + question/reply pair-node back-fill + open-domain prompt flags. Run on LoCoMo conversations 1 to 4 (584 of 1,540 questions, stopped early at your request; conversations 5 to 10 untested).

| Category | n | Old system, Dec 2025 (GPT-4o lenient, with leakage) | New (Gemma lenient) | New (Qwen lenient) | New (Qwen strict) | New (substring) |
|---|---|---|---|---|---|---|
| single_hop | 111 | 47.7 | **75.7** | 62.2 | 33.3 | 21.6 |
| multi_hop | 311 | 75.2 | 72.3 | 66.6 | 41.8 | 27.0 |
| temporal | 130 | 71.5 | **78.5** | 71.5 | 60.8 | 36.2 |
| open_domain | 32 | 53.1 | **62.5** | 43.8 | 6.2 | 3.1 |
| **overall** | 584 | 68.0 | **73.8** | 65.6 | 42.5 | 26.7 |

Honest like-for-like on single_hop, same 111 question ids, same judge:

| Judge | Original flat retrieval | New retrieval stack | Gain |
|---|---|---|---|
| Gemma lenient | 66.7 | 75.7 | +9.0 |
| Qwen lenient | 53.2 | 62.2 | +9.0 |
| Qwen strict | 22.5 | 33.3 | +10.8 |
| substring | 18.0 | 21.6 | +3.6 |

The retrieval gain survives every judge. Nothing else does.

## 2. What increased the score

1. **Per-agent memory routing (biggest).** Treat each speaker as an agent with its own store. Route a question that names one speaker to that speaker's store only. single_hop recall@10 39.4 to 46.8; end to end 64.9 to 73.8 on all 282 single_hop questions. Mechanism, measured: a speaker's own name appears in 0.1% of their messages and the other speaker's name in 33.4%, so a name in the question is an embedding-level pointer to the WRONG speaker. Swapping the name in the question raises pure-embedding recall (26.6 to 34.8). Identity must come from metadata, never text.
2. **Pair nodes.** Embed each reply together with the message before it. As back-fill behind the routed search: +3.2 recall@all at the same candidate budget. As the primary index: hurts (drags the other speaker's message in). Beats graph-neighbour expansion (+1.4) and plain embedding back-fill (+4.1 alone, pairs add +3.2 on top of the local routing).
3. **Open-domain prompt flags.** +14.6 on open_domain under the lenient judge, 0 under the strict judge. The gain is abstentions turned into hedged answers that only a lenient grader credits. Kept behind flags; report it as prompt sensitivity, not as a capability gain.

## 3. What did not work (all on the same 100 single_hop ids, same judge)

| Experiment | Result |
|---|---|
| Graph neighbour expansion (time chain, same-speaker edges, dialogue links) | 66.7 vs hybrid 67.0 vs flat 64.9: no better than plain embedding back-fill; mixing neighbours into the top-50 drops recall@50 from 48.0 to 32.9 |
| Listwise reranker (8 calls instead of 80) | 74 vs 78; rerank latency -56% |
| Answer context 30 / 50 memories | 76 / 69 vs 78 |
| Answer context 10 / 5 memories | 71 / 69 vs 78 (15 plain / 30 list is the optimum) |
| LLM router instead of regex | 79 vs 78 single_hop, 76 vs 78 temporal: null; regex already routes 97% of temporal correctly |
| Keyword-formula fix, softer temporal penalties, no min-charge cutoff | within 0.5 recall: the four scoring stores overlap so much that per-store changes reorder nothing |
| Reply-only pair mapping | -1 to -2 recall@all: the pair link is the only channel for the other speaker's message under local routing |
| Reranker or answer prompt sees the previous message | 71 / 75 / 66 vs 78: dilutes rather than explains |
| 35B answer model instead of 4B | -7 under Gemma judge, +4 under strict independent judge: a judge artifact, null |

Pattern: every gain came from retrieval. Every answer-side change was null or negative. When the gold memory is in the final context the model is right 88.7% of the time; it is absent 29% of the time. Retrieval is the only lever left.

## 4. The judge result (paper-grade)

The same answer files score 13.5 / 21.6 / 51.4 / 64.9% depending only on the grader (substring / strict Qwen / lenient Qwen / lenient Gemma). That 51-point spread is as wide as the published LoCoMo leaderboard spread (58 to 96%) between supposedly different systems. Judge model alone is worth about 12 points with the identical prompt; prompt leniency about 30 points, almost all on multi-item gold answers. A lenient judge from the same model family as the answerer rewards longer, hedged, partial-list answers, which is exactly what prompt-engineering produces. Gemma-lenient vs Qwen-strict agreement: 59%, kappa 0.30.

## 5. Old score vs new score, honestly

- Old reported number: 66.7% overall (GPT-4o lenient judge, Dec 2025), inflated by two leakage channels (gold answer used as an acceptance gate in the speaker-profile path; gold category label used for routing).
- New number on the same 584 questions: 73.8% under a lenient local judge, 65.6% under a lenient independent judge, 42.5% strict.
- The old number and the new lenient number are NOT the same judge, so the +5.8 overall is indicative, not a claim. The +9 single_hop gain against the honest flat baseline under four judges IS a claim.
- Not yet run: conversations 5 to 10 with the new stack (about 2 hours of LM Studio time). Run it before the paper if at all possible: `cd demo && RUN_NAME=capstone_rest ONLY_CONV=4,5,6,7,8,9 RETRIEVAL_MODE=local_pairs OPEN_DOMAIN_KEEP_CONTEXT=1 OPEN_DOMAIN_FORCE_INFER=2 OPEN_DOMAIN_INFER_WORLD=1 ../.venv/bin/python -u runner.py`.

## 6. What to do next to make the system better

1. Retrieval recall ceiling. 25.5% of single_hop gold answers are not a substring of any message (paraphrase, aggregation). The rest: 18 gold messages are dropped by Tesseract's filters before ranking; the embedding back-fill recovers some. Replace the four-store regex fusion with a single learned or BM25+embedding scorer; the audit shows the temporal store dominates the top-10 for every category because fusion weights are near-uniform.
2. Multi-speaker questions. Local routing has no answer for questions naming both speakers or none (157 of 1,219). Federating by charge was worse than pooling because per-store charges are not comparable; normalise per store (rank-based fusion) and retest.
3. Multi_hop is the largest category (841) and the new stack is flat there (72.3 vs old 75.2 under different judges). The pair-node index gave the best multi_hop recall@10 seen (55.4) as a base; a rank-fusion of pairs-as-base with local Tesseract is the one untested combination with signal.
4. Report with a strict independent judge and publish all four scores. It costs 10 minutes per run (script: overnight/n5_rescore.py).
5. Do not spend more time on: context size, routers, listwise reranking, graph-neighbour expansion, bigger answer models. All measured null.

## 7. For the paper (see P_paper_framing.md for the full memo)

Recommended framing: "Signal, not structure": an audited, judge-robust study of what actually helps agent memory retrieval, with (a) the per-agent identity result and its measured mechanism, (b) budget-matched structure-vs-signal comparison where cheap graph edges lose to representation at ingest, (c) a leakage taxonomy and the judge-sensitivity table explaining the leaderboard spread. Fits the Agentic Web workshop's network-as-unit framing through the two-agent split, and its explicit welcome of negative results and benchmarks. Tesseract's hierarchy goes in as pre-registered future work with GO/KILL thresholds from the PDF.

Killer figures: (1) recall@k curves flat / +graph / +embed back-fill / +pairs / local routing at identical candidate budget; (2) the speaker-swap bar chart (recall rises when you name the wrong person); (3) the four-judge table.

## 8. Housekeeping

- The OpenAI key at demo/runner.py:72 in git history is still live unless Ali revoked it. Revoke it.
- Main repo has uncommitted changes: llm_backend.py (LM Studio backend), runner.py (env flags, RETRIEVAL_MODE local_pairs, H5 flags, no key), answering.py, reranker.py, tesseract.py, retrieval_eval.py, .gitignore, .venv. Nothing committed; commit when you have reviewed.
- Each experiment's code lives in its worktree under .claude/worktrees/agent-*/ and its report in this folder. Winners already merged into main: local_pairs mode, H5 flags.
- All per-question results: demo/results/*.json and this folder's *.json.

## Addendum (16:55): "mega search" = vector + BM25 + entity-graph hop + Tesseract, RRF-fused
Built after the report at the user's request (NeuralGraph/mega_search.py, RETRIEVAL_MODE=mega). Retrieval-only it is the best head ranking of the night (recall@10 53.7 vs 46.8 on 1,219 q; multi_hop +8.8; BM25 channel adds nothing). End to end on the same 100 ids it LOSES on single_hop (68 vs 78, 3 wins / 13 losses) and ties on multi_hop (76 vs 75). Accuracy-given-gold-in-context fell from 88.6 to 79.1: the entity hop fills the 80-candidate window with topically related, non-answer messages that the reranker and answer model prefer over the answer. Lesson for the paper: substring recall@k is not a sufficient proxy; candidate-set precision matters as much as recall for a small reranker. Keep local_pairs as the shipped retriever; the graph hop is worth revisiting only with a stronger reranker or as a multi_hop-only channel.

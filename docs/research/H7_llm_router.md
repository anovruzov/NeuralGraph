# H7: LLM router vs regex router

**Verdict: DOES NOT WORK (null).** single_hop +1 (79 vs 78), temporal -2 (76 vs 78); both inside noise (100 q, ~±4 pt). Adds ~110 ms/question. The regex cascade already sends 97% of temporal questions to TEMPORAL, so there was no headroom where routing was expected to matter; on single_hop the routers disagree on 48% of questions and still tie.

Setup: `ROUTER=llm` = one Gemma-4-e4b call per question (temperature 0, max_tokens 10, 5-mode prompt, first mode word parsed, STRICT fallback; 0 parse failures in 200 calls), cached per question in `demo/results/route_cache_llm.json`. Control `ROUTER=regex` = today's cascade. Everything after the router (open-domain checks, 30-memory LIST/AGGREGATION context, extractors, gemma judge, local_pairs retrieval) identical. Same 100 ids per pair.

## Results

| category | router | acc | mean e2e | route ms | agreement |
|---|---|---|---|---|---|
| single_hop | regex | 78/100 | 8027 | 0 | |
| single_hop | llm | **79 (+1)** | 8115 (+88) | 105 (p95 123) | 52% |
| temporal | regex | 78/100 | 7556 | 0 | |
| temporal | llm | **76 (-2)** | 7617 (+61) | 119 (p95 122) | 97% |

single_hop: 3 wins / 2 losses; temporal: 0 wins / 2 losses. Gold-in-final-context unchanged (16 vs 15%, 3 vs 3%): the router cannot change retrieval, only prompt and context size.

## Mode distribution (router output, before open-domain checks)

| | STRICT | TEMPORAL | LIST | AGGREGATION | INFERENTIAL |
|---|---|---|---|---|---|
| single_hop regex | 10 | 6 | 35 | 37 | 11 (+1 ADVERSARIAL) |
| single_hop llm | 11 | 4 | 53 | 18 | 14 |
| temporal regex | 1 | 97 | 0 | 1 | 1 |
| temporal llm | 2 | 97 | 0 | 1 | 0 |

single_hop disagreements (48): AGGREGATION->LIST 16 (ctl 14 vs trt 15 correct), AGGREGATION->INFERENTIAL 6 (5 vs 6), INFERENTIAL->AGGREGATION 4 (1 vs 1), rest <=3 each and tied. On the 48 disagreeing ids: 39 vs 40.

Surprise: the regex router labels 72% of single_hop questions LIST/AGGREGATION (30-memory context) and only 10% STRICT ("What did Caroline research?" is AGGREGATION). The LLM also prefers LIST (53%). The STRICT prompt serves ~1 in 10 single_hop questions under either router; the actual lever is context size (H2), not prompt choice.

## Examples
- WIN: "Who gave Maria's family money when she was younger..." gold "Her aunt". regex->TEMPORAL "4 August 2023"; llm->STRICT "auntie".
- WIN: "What is something Nate gave to Joanna that brings her joy?" gold "stuffed toy pup". regex->AGGREGATION "Not found"; llm->INFERENTIAL "stuffed animal".
- WIN: "What people has Maria met and helped while volunteering?" regex->AGGREGATION "shelter residents, kids"; llm->LIST "Jean, kids, veterans, Laura..." (partial, judge accepted).
- LOSS: "Where did Caroline move from 4 years ago?" gold "Sweden". llm misread "4 years ago" as TEMPORAL -> "week of 29 May 2023". Control said "home country" (wrong too, judged correct).
- LOSS: "How many times has Melanie gone to the beach in 2023?" gold "2". llm->AGGREGATION "Not found"; regex->TEMPORAL "week of 14 August 2023" judged correct (judge false positive).
- LOSS (temporal): "How many weeks passed between Maria adopting Coco and Shadow?" gold "two weeks". llm->AGGREGATION "Not found"; regex->TEMPORAL extractor "around 28 July 2023" judged correct (leniency).

Two of four losses are judge false positives on the control side; true delta ~0.

## Code (worktree agent-a18c430e6be535102, uncommitted)
- `demo/runner.py:137-191` ROUTER env, cache load/save, `LLM_ROUTER_PROMPT`, `llm_route_question()`.
- `demo/runner.py:876-888` routing site: regex cascade kept and recorded as `regex_mode`; `ROUTER=llm` overrides `query_mode`/`list_question`; open-domain checks after it unchanged.
- `demo/runner.py:1093-1100` result rows: `router, regex_mode, llm_mode, route_mode, query_mode, route_latency_ms, route_cached`; `:303` cache persisted with `save_results`; `:1169` summary.
- Scripts `overnight/patch_h7.py`, `run_h7.sh`, `analyze_h7.py`; results `H7_{sh,tmp}_{control,treatment}.json`, `H7_route_cache_llm.json`.

Cost: 4 x 100 q, ~14 min each (lock 09:54-10:50). Router adds ~110 ms/q (1.3% of e2e).

Recommendation: keep `ROUTER=regex`. Routing is not the bottleneck; instead ask why 72% of single_hop gets a 30-memory context and whether STRICT-for-single-fact would do as well.

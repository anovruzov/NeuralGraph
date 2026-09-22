# Experiment reports

One file per experiment from the September 2026 retrieval campaign, kept **verbatim** as
the record of what was run — including the paths and line numbers of the repository as it
stood at the time. See [the path mapping](../README.md#reading-the-reports) for where
those files live now.

| Report | Question | Verdict |
|---|---|---|
| [`REPORT.md`](REPORT.md) | The campaign, end to end | — |
| [`RESULTS_ALL.md`](RESULTS_ALL.md) | Every number, every run | — |
| [`AUDIT.md`](AUDIT.md) | What actually runs, and what leaked | — |
| [`H9_two_agent.md`](H9_two_agent.md) | Route questions to the named speaker's own store? | **works** (+8.9 e2e) |
| [`H3_pairs.md`](H3_pairs.md) | Question/reply pair nodes as back-fill? | **works** (+3.2 recall) |
| [`H5_open_domain.md`](H5_open_domain.md) | Open-domain prompt treatments? | lenient-judge only |
| [`H1_listwise_rerank.md`](H1_listwise_rerank.md) | One listwise rerank call instead of 50? | faster, less accurate |
| [`H2_context_size.md`](H2_context_size.md) / [`N1_small_context.md`](N1_small_context.md) | More or less context? | hurts either way |
| [`H4_H6_scoring.md`](H4_H6_scoring.md) | Fix the keyword and temporal scoring? | within noise |
| [`H7_llm_router.md`](H7_llm_router.md) | Let an LLM route the query? | null |
| [`H8_answer_model.md`](H8_answer_model.md) | A 35B answer model? | judge artifact |
| [`N3_replyonly_pairs.md`](N3_replyonly_pairs.md) | Reply-only pairs? | hurts |
| [`N4_pair_rerank.md`](N4_pair_rerank.md) | Pair-aware reranker and prompt? | hurts |
| [`N5_judge_sensitivity.md`](N5_judge_sensitivity.md) | How much does the grader decide the score? | **51-point spread** |
| [`QUERY_ROUTING.md`](QUERY_ROUTING.md) | The hashtable marker system for query routing | — |
| [`P_paper_framing.md`](P_paper_framing.md) | Venue fit and framing | — |

# R3: what competitors credit on LongMemEval, and what transfers to a local 9B reader

Researched 2026-10-28 (web + local files). Read-only research; nothing implemented. Estimates marked "est." are mine.
Verification column: **P** = I read the primary text (PDF, repo file, raw results JSON); **W** = vendor web page read through a summarising fetch, key numbers cross-checked against a second source where noted; **S** = search snippet only (treat as unconfirmed).

## 0. Sources (cited below as [id])

| id | URL |
|---|---|
| LME | https://arxiv.org/abs/2410.10813 (PDF v2: https://arxiv.org/pdf/2410.10813v2) |
| LMEJ | https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py (official judge prompts) |
| ZEP | https://arxiv.org/abs/2501.13956 |
| MEM0P | https://arxiv.org/abs/2504.19413 (Mem0 / Mem0-graph paper, LoCoMo only) |
| MEM0D | https://docs.mem0.ai/core-concepts/memory-evaluation |
| MEM0R | https://github.com/mem0ai/memory-benchmarks (README, `results/platform/longmemeval_results.json`, `benchmarks/longmemeval/prompts.py`) |
| MEM0B | https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm and https://mem0.ai/blog/the-token-efficient-memory-algorithm-now-has-temporal-reasoning |
| MAS | https://mastra.ai/research/observational-memory and https://mastra.ai/blog/observational-memory |
| SUP | https://supermemory.ai/research/longmembench (ASMR post: https://supermemory.ai/blog/we-broke-the-frontier-in-agent-memory-introducing-99-sota-memory-system/, now unreachable; aggregation post: https://supermemory.ai/blog/solving-the-precision-recall-tradeoff-search-result-aggregation/) |
| HIND | https://arxiv.org/abs/2512.12818 (PDF https://arxiv.org/pdf/2512.12818); table mirror https://github.com/vectorize-io/hindsight-benchmarks |
| LET | https://letta.com/blog/benchmarking-ai-agent-memory |
| ZEN | https://arxiv.org/pdf/2604.23878 (third party, same-protocol k=5 comparison incl. Letta, A-Mem, Mem0) |
| AMEM | https://arxiv.org/abs/2502.12110 |
| MBASE | https://github.com/memodb-io/memobase |
| LANG | https://langchain-ai.github.io/langmem/concepts/conceptual_guide/ |
| EMG | https://www.emergence.ai/blog/sota-on-longmemeval-with-rag |
| MF | https://arxiv.org/abs/2605.23986 (MemForest; third party, small Qwen3 readers, LME-S) |
| SURV | https://arxiv.org/abs/2604.01707 (modular memory survey, Qwen3.5-9B vs 27B) |
| REL | https://braindetox.kr/en/posts/memory_benchmark_scores_reliability_2026.html (S only: judge prompt copied across vendors; dataset revised in 2025) |
| local | `research/overnight/REPORT.md`, `hypotheses/R1_failure_analysis.md`, `hypotheses/R2_extraction_levers.md`, `docs/BENCHMARKS.md` Track D |

## 1. Why our 62% strict is not comparable to 85-95%

| factor | competitors | us | est. size of the gap |
|---|---|---|---|
| Dataset variant | mostly **S** (~115k tokens, ~40-50 sessions) [LME]; Oracle = evidence sessions only [LME] | Oracle 50-q | Oracle is the easy variant: GPT-4o 0.87-0.92 Oracle vs 0.61-0.64 S [LME Fig 3b]; o3 Oracle 92.0 [EMG] |
| Reader | GPT-4o / GPT-5 / GPT-5-mini / Gemini-3-Pro / o3 | Qwen3.8-9B local | same memory, +10.6 pts from gpt-4o to gpt-5-mini [MAS]; 17 pts GPT-4o vs Llama-8B on Oracle with CoN+JSON [LME Fig 6] |
| Judge | official `get_anscheck_prompt` with GPT-4o: "contains the correct answer OR the intermediate steps", **off-by-one days tolerated** on temporal, previous info allowed on knowledge-update, **preference graded on "uses the user's personal info", not rubric coverage** [LMEJ] (P) | strict = every gold fact present; lenient credits "I don't know" | our strict-vs-lenient spread is 26 pts (62 vs 88); R1 counted 7 lenient IDK credits and 2 judge false negatives among 23 strict misses (BENCHMARKS.md Track D estimates ~5 of 20) |
| Reader prompt | Mem0 ships a 14-rule benchmark-tuned prompt with a `<mem_thinking>` scratchpad and "second full scan" for counting [MEM0R prompts.py] (P) | concise prompt + IDK cascade | not ablated by anyone; Emergence sees +3.6 pts on full-context GPT-4o from prompt alone (60.2 -> 63.8) [EMG] |
| Retrieval budget | Mem0 top-200 memories, ~6.8k tokens [MEM0D, MEM0R]; Zep 20 facts/entities, 1.6k tokens [ZEP]; Mastra: whole dated observation log, ~30k tokens [MAS] | 15 memories + 1 quote, 3600 chars (~0.9k tokens) | strong readers use 2-30k tokens; the 8B reader in [LME §5.2] drops sharply beyond ~3k retrieved tokens |

Honest comparison target for our column: a **lenient-non-IDK** (or official-prompt) score on Oracle, versus GPT-4o Oracle 82.4 [EMG] / 87.0 [LME]. Our LME-50 lenient-non-IDK was 50 -> 64 with the shipped change (REPORT.md sec. 4); that, not 62 vs 94, is the gap to close.

## 2. Per-system table: number, reader, judge, variant (item 1a)

| system | LME number reported | variant | reader / answer model | judge | verif. |
|---|---|---|---|---|---|
| **LME paper, best config** | QA (GPT-4o / L3.1-70B / L3.1-8B): **M**, round value, K=V+fact, top-10, CoN+JSON: 0.720 / 0.682 / 0.572 (Table 3); **Oracle** CoN+JSON: 0.924 / 0.848 / 0.756 (Fig 6); S full-context: 0.606 (0.640 CoN) GPT-4o, 0.454 Llama-8B | M, S, Oracle | GPT-4o, Llama 3.1 70B/8B, + 5 small models (App. E.1) | gpt-4o-2024-08-06, >97% agreement with humans | P [LME] |
| **Zep / Graphiti** | **71.2** (gpt-4o), **63.8** (gpt-4o-mini); full-context 60.2 / 55.4 | S | gpt-4o-2024-11-20, gpt-4o-mini; graph built with gpt-4o-mini | GPT-4o, official per-type prompts | P [ZEP] |
| **Mem0 (managed v3, Apr-May 2026)** | **94.4** (top-200), 94.8 (top-50); previous algorithm 67.8; 93.4 before the temporal update | 500 q, per-question haystack (S-style) | **gpt-5** (metadata in results JSON); docs do not name the reader | not stated in results file; OSS runs use GPT-5 as answerer and judge | P [MEM0R json], W [MEM0B, MEM0D] |
| Mem0 OSS (self-hosted, same repo) | 91.0 (GPT-5 extractor), 89.8 (GPT-OSS-120B), 88.6 (Llama-4 Maverick / Gemma-4-31B) | same | GPT-5 | GPT-5 | P [MEM0R README] |
| Mem0 old OSS, other people's runs | 31.8 (k=5, gpt-4o-mini) [ZEN]; 44.8 / 50.2 / 59.2 (Qwen3-4B / Qwen3-30B-A3B / Gemma-4-12B) [MF] | S | small | gpt-4o-mini [ZEN]; deepseek-v4-flash [MF] | P |
| **Mem0-graph** | no LME number. Paper (LoCoMo only): J 68.44 vs 66.88 for plain Mem0; graph helps temporal (58.1 vs 55.5); [SURV] argues triple-only graph memory loses raw detail ("may explain why Mem0 outperforms Mem0g in many cases") | LoCoMo | gpt-4o-mini | LLM judge J | P [MEM0P], [SURV] |
| **Mastra Observational Memory** | **94.87** (gpt-5-mini), 93.27 (gemini-3-pro), **84.23** (gpt-4o); Mastra RAG topK-20 80.05 | S (`longmemeval_s`, 500 q) | gpt-5-mini / gemini-3-pro / gpt-4o; observer+reflector gemini-2.5-flash at ingest | gpt-4o, official prompts | W [MAS] |
| **Supermemory** | gpt-4o **81.6**, gpt-5 **84.6**, gemini-3-pro **85.2** (numbers quoted by Hindsight from the Supermemory report). Supermemory's own page now shows gpt-4o **97%** (labelled "Recall@20 with aggregation", yet the same figure appears in its LLM-judge table, and gpt-5/gemini rows are far lower: internally inconsistent). ASMR experimental flow ~99% (8-variant ensemble 98.6) | S | gpt-4o, gpt-5, gemini-3-pro | gpt-4o | W [SUP]; ASMR S (vendor says "not our production engine"; one summary calls the post a parody) |
| **Hindsight** | **83.6** (OSS-20B), **89.0** (OSS-120B), **91.4** (Gemini-3 as answerer only) | S | GPT-OSS-20B/120B for memory and reading; Gemini-3 swapped in for answers | **GPT-OSS-120B** (not GPT-4o: the Supermemory/Zep rows in its table are borrowed from GPT-4o-judged runs) | P [HIND] |
| **Letta / MemGPT** | none published. LoCoMo 74.0 with gpt-4o-mini agent using grep/search_files. Third party (k=5 retrieval, gpt-4o-mini, S): 42.8; 59/500 queries errored | n/a | gpt-4o-mini | gpt-4o-mini | P [LET], [ZEN]; Zep could not run MemGPT on LME [ZEP] |
| **A-Mem** | none by authors (LoCoMo/F1). Third party k=5: 35.4 | S | gpt-4o-mini | gpt-4o-mini | P [AMEM], [ZEN] |
| **Memobase** | none. LoCoMo 75.78 (v0.0.37, as tabulated by Hindsight from the Backboard run) | n/a | n/a | n/a | W [MBASE], [HIND] |
| **LangMem** | none. LoCoMo 58.10 (same table) | n/a | n/a | n/a | W [LANG], [HIND] |
| Emergence (extra) | 79.0 / 82.4 / 86.0 (Simple Fast / Simple / Internal); naive turn-RAG 52.0; Oracle GPT-4o 82.4; Oracle o3 92.0 | S (+Oracle) | gpt-4o-2024-08-06, o3 | not stated | W [EMG] |
| MemForest, LightMem, EverMemOS (extra, third party) | Qwen3-4B / 30B-A3B / Gemma-4-12B: MemForest-Planner 72.6 / 81.8 / 77.8; Zep-local (Graphiti) 69.6 / 71.0 / 75.6; EverMemOS 61.2 / 67.0 / 78.0 | S | **small open readers**, Qwen3-Embedding-0.6B | deepseek-v4-flash, public prompt | P [MF] |

## 3. Per-system design table (item 1b)

| system | what is stored | retrieval (embedding / hybrid / rerank / k / time) | reading (render, decomposition, reasoning) |
|---|---|---|---|
| LME paper best | raw **rounds** (user side only as keys), key = value + LLM-extracted **user facts**; also timestamped events; session summary/keyphrase variants tested | Stella-V5 1.5B dense (BM25, Contriever compared: dense >> BM25, App. E.2); top-5/10; **time-range query expansion** by LLM (needs GPT-4o; Llama-8B hallucinates ranges, Table 11); key merging beats rank merging | items **sorted by timestamp**, JSON, **Chain-of-Note** (extract per item, then reason), "Current Date" given [LME §5.1, 5.5, Fig 13] (P) |
| Zep / Graphiti | temporal KG: episodes (raw), entity nodes + summaries, fact edges with `valid_at`/`invalid_at` (superseded facts invalidated), communities | BGE-m3 embed + BGE-m3 reranker; cosine + BM25 + BFS, RRF/MMR/episode-mentions/node-distance/cross-encoder rerankers; **top-20 edges+nodes, 1.6k tokens** | `FACT (Date range: from - to)` + entity summaries; no decomposition [ZEP] (P). Weakness: single-session-assistant 81.8 -> 75.0 (4o-mini), 94.6 -> 80.4 (4o) because facts replace raw assistant turns |
| Mem0 v3 | ADD-only single-pass fact extraction (nothing overwritten), entity links, write-time temporal metadata (event time, state key, closed `event_end`); chunks of 2 messages at ingest | semantic + BM25 (lemmatised) + entity match + additive temporal score fused by rank; **top-200**, ~6.8k tokens; decay boost (up to 1.5x recent) | memories **newest-first, grouped by date heading**; benchmark-specific long prompt with `<mem_thinking>`, explicit counting/most-recent-wins/abstention rules [MEM0R, MEM0B] (P) |
| Mem0-graph | triples (entity, relation, entity) beside text memories | entity-centric traversal + triplet embedding; top-s memories | plain context [MEM0P] |
| Mastra OM | no retrieval: Observer compresses messages (~30k-token threshold) into **dated, prioritised text observations**; Reflector (40k) merges/drops superseded | none per turn; whole log in context (~30k tokens avg) so prefix is cacheable | observations grouped by date with 3 dates each (observed, referenced, relative) [MAS] (W); no ablations published |
| Supermemory | semantic chunks of whole sessions -> **atomic memories** (ambiguity resolved) + **source chunk kept attached**; relations updates/extends/derives; `documentDate` + `eventDate` | "hybrid" semantic search (components undisclosed), **k=20**, source chunk injected with each hit; result "aggregation" (a slot = a synthesis of several docs) instead of reranking | memory + chunks + temporal context + profile; short prompt, question date supplied [SUP] (W) |
| Hindsight | **coarse narrative facts** (2-5 per conversation, cross-turn context kept), 4 networks (world/experience/opinion/observation), entity graph, time-normalised, human-readable time ref added before embedding | 4 channels: dense, BM25, graph spreading activation, **temporal** (rule-based parsers + flan-t5-small range) -> RRF -> **cross-encoder MiniLM-L6** -> token budget | agentic "reflect" step conditioned on a disposition profile [HIND] (P) |
| Letta / MemGPT | core blocks + archival + (filesystem files); agent decides | agent-issued `grep` / semantic `search_files`, iterative until `answer_question` | tool loop; "how the agent uses retrieval matters more than the mechanism" [LET] |
| A-Mem | LLM-written notes (context, keywords, tags), links, evolution of old notes | embedding similarity over notes | plain [AMEM] (abstract only) |
| Memobase | per-user profile slots (topic/sub_topic) + event-gist timeline; raw blobs deleted by default | SQL + embeddings; `context` API packs profile+events | profile-first prompt [MBASE] |
| LangMem | semantic (collection or profile), episodic, procedural; hot-path or background | LangGraph store: semantic search + metadata filter; strength = similarity x importance x recency | n/a [LANG] |
| Emergence | raw turns; match turns, **retrieve whole sessions** (score = NDCG of turn scores after cross-encoder rerank) | 42 turns (Fast) or 20 (Faster); Fast uses two LLM calls | step-by-step CoT / extract-events call then answer [EMG] |

## 4. Techniques repeatedly credited, with effect sizes (item 2)

"Reader-confounded" = the cited gain was measured with a GPT-class reader and may shrink for a 9B.

| technique | who credits it | reported effect | reader-confounded? | verdict for us |
|---|---|---|---|---|
| **Raw turns / source text kept next to extracted facts** | LME (value = round, K=V+fact); Supermemory (source chunk injected); Zep's own SSA regression when only facts are shown; SURV ("retain raw messages ... and original conversation at generation"); Mem0 old algorithm SSA 46.4 -> 98.2 once assistant-side facts are extracted (W, [MEM0B]) | LME: fact-only value lowers QA except multi-session; K=V+fact top-10 GPT-4o .670 -> .720, 70B .624 -> .682, 8B .534 -> .572. Zep SSA -9 to -18 pts vs full context. Ours: quotes 42 -> 50 strict (5:1) | partly (8B gain is smaller than GPT-4o's) | already shipped as 1 quote; the **episodic raw-message block is the untested extension** |
| **Fact-augmented key expansion** (K = V + fact) | LME | recall@k +9.4%, QA +5.4% avg over GPT-4o/70B/8B; with 8B session-top-5 only +0.6 (.524 -> .530). Fact-only keys do not help; **summary/keyphrase expansion is neutral to negative** (session top-10 GPT-4o .676 -> .666 with summary; keyphrase-only key GPT-4o .425 / .489 vs K=V .615 / .670 at top-5 / top-10) | small for 8B | our index is fact-only (the weak variant); expanding the key with the source quote is the cheap analogue |
| **Session summaries alongside facts** | nobody credits it on LME; Mastra's observations are dated compressions, not summaries | LME Table 3: V+summary does not beat V+fact | n/a | do not build |
| **Time-stamping facts / event vs mention dates** | Zep (valid date range), Mem0 (event time, state keys), Supermemory (document/event date), Hindsight (normalised ranges + time text embedded), Mastra (3 dates) | Zep TR 36.5 -> 54.1 (4o-mini) / 45.1 -> 62.4 (4o) incl. graph; Mem0 TR 51.1 -> 93.2 (old -> v3, many changes), then +3.8 from additive temporal scoring; **no vendor publishes a clean ablation** | yes | we already carry event_time + said-on date; "saidon"/weekday/calendar renders were noise (REPORT.md) |
| **Time-aware retrieval / query time-range** | LME, Hindsight, Mem0 (additive, never filters) | LME: recall +11.3% (round) / +6.8% (session) with GPT-4o as range extractor; with Llama-8B as extractor ~0 or negative (round K=V+fact @5: .489 -> .526 GPT-4o, .481 Llama-8B) | **yes: needs a strong query model**; Hindsight uses rules + flan-t5-small instead | use rules, not the 9B |
| **Query rewriting / decomposition for multi-session** | Mem0 (scan prompt over top-200), Supermemory ASMR (parallel search agents, 11-15 LLM calls), Letta (iterative search), Emergence Accumulator (81.8 at 112 s/q) | no clean ablation; ASMR/Accumulator are many-call systems | yes | rule-based sub-queries only |
| **Reranking** | Hindsight (cross-encoder MiniLM after RRF), Zep (BGE-m3), Emergence (cross-encoder) | no ablation published. Supermemory argues reranking hurts recall at fixed k and replaces it with aggregation | n/a | low value on Oracle (turn@15 already 93-96) |
| **Larger k + strong reader** | Mem0 top-200 (top-50 gives the same 94.8 vs 94.4), Mastra RAG topK 20 = 80.05 (< OM 84.23), Emergence 42 -> 20 turns: 79.0 -> 76.8 (-2.2) | LME: GPT-4o keeps improving beyond 20k tokens, **Llama-8B degrades beyond ~3k** | **yes, fully** | do not copy; our own k20/7000 was -1 q at n=20 |
| **Chain-of-Note / reasoning reader** | LME, Emergence (CoT; Fast uses an extract call first), Mem0 prompt, Hindsight reflect | LME Oracle: GPT-4o 0.822 -> 0.924 (JSON+CoN best); 70B .762 -> .848; **Llama-8B .710 -> .756 (+4.6)**; JSON helps only together with CoN. Bar order in Fig 6 inferred from the printed values; Table 3b lists 8B as 0.710 both with and without CoN | mostly | our compute-primary prompt was a trade (strict +2, lenient -8); cascade is the winning form |
| **Sort by time / group by date** | LME (always sorted by timestamp), Mem0 (newest-first grouped by date), Mastra (date sections) | no ablation anywhere | partly | untested for us; our block is relevance-ordered |
| **Stronger reader** (not a memory technique) | every table | Mastra +10.6 (4o -> 5-mini, same memory); Zep +7.4 (4o-mini -> 4o); Emergence Oracle +9.6 (4o -> o3), full-context +12.2; Supermemory +3.0-3.6; Hindsight 120B -> Gemini-3 answerer +2.4 | **100%** | cannot be bought; see sec. 7 |
| **Compress the whole history into dated observations** | Mastra | 84.23 vs 80.05 RAG (+4.2) vs 82.4 Oracle, gpt-4o; no ablation of any component | yes | n/a (needs ~30k-token context) |
| **ADD-only / invalidate instead of overwrite** | Mem0 (ADD-only), Zep (`invalid_at`), Supermemory (updates relation), Mastra (Reflector drops superseded) | Mem0 KU 96.2 -> 93.6 after the temporal update (-2.6, W): not clearly a win; KU is the lowest non-multi-session cell for ADD-only | yes | our numeric-dedupe guard is the same idea; within noise |

## 5. Ranked techniques for our setup (item 3)

Setup: Qwen3.8-9B reader, strict judge, harness `research/overnight/scripts/answer_eval.py`, contexts from `chat-memory-context` with `NEURALGRAPH_EXP` overrides (documented in `crates/neuralgraph-memory/src/service.rs:100-150`). Question mix in LME-50: TR 11, MS 12, KU 7, SSU 7, SSA 6, SSP 3, abstention 4 (R1). 1 q = 2 pts; reader/render comparisons are paired on identical DBs (immune to the ±12-pt ingest floor); an effect needs ~4-5 net flips to be believable. Gains are est., **not additive**; plausible combined haul of the first seven is +2 to +5 q.

| # | technique | cost | targets | est. gain /50 | risk | reader-driven in competitors? |
|--:|---|---|---|---:|---|---|
| 1 | Episodic raw-message block (BM25 over message store, user + assistant) with a bigger budget | minutes (config) | SSA, MS, TR "fact-not-in-context" | +1..3 | context bloat hurts the 9B | no (LME, Supermemory, Zep SSA, SURV) |
| 2 | Chronological / date-grouped block with a one-line legend | minutes (harness) | TR, KU, MS | +0..2 | loses relevance order the prompt promises | partly |
| 3 | Official-protocol judge (type-specific prompts, IDK never credited) as an extra column | minutes | TR, SSP, KU | 0 real; moves reported number +3..6 est. | none | no (measurement) |
| 4 | Routed notes-then-answer (CoN) pass for duration/count/sum questions + JSON-ish lines | minutes-1 h (harness) | MS, TR | +0..2 | R4 routing was noise (50 vs 52); compute lost lenient | mostly |
| 5 | Rule-based temporal prior (additive boost by parsed query range vs `event_time`) | 1 h (`retrieval.rs`) | TR | +0..1 (Oracle); +2..4 on S | wrong parse demotes the right memory; additive only | LME needs GPT-4o; rules avoid it |
| 6 | Rule-based multi-query for aggregation questions (second retrieval per entity/noun phrase, RRF-merged, cap 20) | 1-2 h | MS (12 q) | +0..2 | more lines, more distraction | partly |
| 7 | Key expansion at embedding time (memory text + source quote), BM25 over both | 1 h + re-embed only (deterministic, no re-extraction) | retrieval misses, KU | +0..1 (Oracle) | re-embedding changes all ranks | LME shows smaller gain for 8B |
| 8 | Routed larger budget (k 20-25, 6-7k chars) for count/aggregation questions only | minutes | MS | +0..1 | k20/7000 was -1 q on LME-20 | **yes** |
| 9 | Contradiction-preserving reconcile (keep both values, newer flagged; Mem0/Zep/Supermemory style) | 2 h per ingest; needs deterministic mode | KU (7 q) | +0..2 | numeric-dedupe fix already showed only noise | yes |
| 10 | Cross-encoder rerank after RRF (Hindsight/Zep style) | 1-2 h + model | retrieval misses | +0..1 | Supermemory reports recall loss at fixed k | no |

### Detail (mechanism and exact change)

1. **Episodic raw block.** The crate already has it, default off (`retrieval.rs` `append_raw_messages`). Mechanism: facts are lossy (R2: 7 of 23 misses have a specific missing; 41275add and 7161e7e2 are assistant-said tables/titles never extracted); raw turns are what LME's best config indexes and what Supermemory/Hindsight keep attached. Change: build contexts on `lme_base2` (and det1/det2 when done) with
   `NEURALGRAPH_EXP=context_quotes=1,context_raw_messages=3,context_raw_message_chars=600,context_raw_include_assistant=1,context_max_chars=4800 chat-memory-context lme --db-dir ... --n 50 --seed 1`
   then the `shipped_eval.sh` answer protocol (concise + `--cascade auto_v1n`). Arms: user-only vs +assistant; chars 400 vs 600 (assistant tables are cut at 400 by default). Raw lines only use budget left after memories and quotes, so **raise `context_max_chars` or the block stays empty**. Caveat: on Oracle the haystack is 1-3 sessions, so BM25 over messages is unusually easy; on S/M it would be the weakest channel (no vector leg). Adding a vector leg over messages is a 1-2 h follow-up.
2. **Chronological, date-headed block.** LME always feeds items "sorted by their timestamp" [LME §5.1]; Mem0 sorts newest-first and groups by date with the rule "memories under the same date heading are from the same conversation" [MEM0R prompts.py]. Change: add `--order chrono|newest|relevance` and `--group-dates` to `render_memories` (after top-k selection, sort by `event_time or said-on`); change the prompt line "most relevant first" to match. Targets "which is the latest value" (KU) and ordering (`gpt4_7f6b06db`). Run both orders; Mem0's newest-first plus "latest value replaces earlier" is the KU-friendly one.
3. **Official judge column.** Port `get_anscheck_prompt` ([LMEJ]) into `answer_eval.py` as `lme_official` (and the abstention prompt for the 4 `_abs` rows). Effects on our failures: `a2f3aa27` "approximately 1300", off-by-one day answers on TR, preference rows (3 q, currently 0/3 strict). Report next to strict_v2. Without it the 62% cannot be placed against [ZEP]/[MAS]/[SUP]. Not a memory gain.
4. **Routed notes-then-answer.** LME's CoN is "extract info from each item, then reason" and JSON-wrapped items; it gave GPT-4o +10 and the 8B only ~+4.6. Emergence Fast makes the extract call separate. Change: for questions matching the existing `COMPUTE_RE` run call 1 = "list the relevant lines verbatim with dates as JSON" (max 8 lines), call 2 = answer from that list with the `auto_v1n` rules. Keep concise-first for lookups. Honest expectation is small because the cascade already captured the cheap part.
5. **Temporal prior.** Mem0's temporal score is additive, never a filter ("nudges ranking ... without filtering candidates out") [MEM0D]; Hindsight parses ranges with rules and a tiny seq2seq model, not the main LLM [HIND]; LME shows an 8B range extractor fails (false-positive ranges on 3 of 4 examples, Table 11) [LME]. Change in `retrieval.rs`: parse "last Saturday / two months ago / in March / last week" with `temporal.rs` + question date, and multiply the score by (1 + w) when the memory's `event_time` interval intersects the range (w ~ 0.3, test 0.15-0.5). On Oracle the evidence is already retrieved (turn@15 93-96), so this mainly matters when moving to S.
6. **Rule-based multi-query.** R1's cause table: 4 "second half of the evidence missing" rows (372c, 2ebe, 08f4, 0f05). Change: for count/sum/duration questions extract noun phrases + entities with the existing alias/entity code, run one extra search per phrase (k=5 each), RRF-merge with the main list, cap the block at 20 lines. Do not use the 9B to generate sub-queries (small models are unreliable query rewriters per [LME]).
7. **Key expansion.** LME's K=V+fact is "document expansion": concatenate compressed text with the original value before indexing. Ours is the inverse (the compressed fact is the key). Change: re-embed each memory as `text + " || " + first human source quote (<=300 chars)` and index BM25 over the same string; keep the displayed line unchanged. Embeddings are deterministic, so this avoids the ±12-pt ingest noise. Expect little on Oracle.
8. **Routed larger budget.** Copying "k=50-200" is the clearest reader-driven trap: Mem0 reports top-50 = top-200 with GPT-5, LME shows the 8B collapses past ~3k tokens, our LME-20 k20/7000 run was 30 vs 35 strict. Only for questions with "how many / total / altogether", only if #6 shows missing items.
9. **Contradiction-preserving reconcile.** Mem0's ADD-only plus state keys, Zep's `invalid_at`, Supermemory's `updates` relation all keep both values and mark which is current. Ours drops a near-duplicate with a different number (0f05491a: 125 shown, 120 correct). The `num_dedupe` fix already looked like noise (strict +6 / strict_v2 -8). Do this only inside the deterministic-mode harness (det1/det2 runs in RUNLOG), >= 2 ingests per arm.
10. **Cross-encoder rerank.** Hindsight, Zep and Emergence use one, none ablates it, and Supermemory argues against it. Our retrieval is not the bottleneck on Oracle. Defer to the S/M move.

## 6. Which competitor gains are the reader, not the design (item 3 flag)

| claim | memory-design part | reader part | source |
|---|---|---|---|
| Mastra 94.87 | OM vs RAG topK-20 at gpt-4o: **+4.2**; vs full-context gpt-4o +24.0; vs Oracle +1.8 | gpt-4o -> gpt-5-mini on the same memory: **+10.6** (TR +9.8, SSP +26.7, SSA +12.5) | [MAS] |
| Mem0 94.4 | 67.8 -> 93.4 (algorithm; reader and prompt not stated as fixed) | GPT-5 reader, top-200, 14-rule `<mem_thinking>` prompt; OSS extractor swap costs only 1.2-2.4 pts (91.0 vs 89.8/88.6) so the reader/prompt dominate | [MEM0R] |
| Supermemory 85.2 | memory vs full-context gpt-4o: 81.6 vs 60.2 (+21) | gpt-4o -> gemini-3-pro: +3.6; the 97% row is not usable | [SUP, HIND] |
| Hindsight 91.4 | OSS-20B memory+reader 83.6 vs the same model full-context **39.0** (+44.6) | 20B -> 120B +5.4, 120B -> Gemini-3 as answerer +2.4 | [HIND] |
| Zep 71.2 | +11.0 over full-context gpt-4o, +8.4 over full-context 4o-mini | 4o-mini -> 4o: +7.4 | [ZEP] |
| Emergence 82.4 / 86.0 | Simple matches Oracle GPT-4o | Oracle GPT-4o 82.4 -> o3 92.0 | [EMG] |
| LME best config | K=V+fact: +5.4 pts QA avg | GPT-4o vs Llama-8B at equal memory: 0.720 vs 0.572 (M), 0.924 vs 0.756 (Oracle) | [LME] |

The only clean "small reader, big gain from design" data points are Hindsight OSS-20B (3.6B active parameters, MoE; extractor and reader are the same model) at 83.6 and the MemForest runs below.

## 7. What a 9B reader cannot do (item 4)

**The LME paper has no per-question-type table for readers** (I checked the PDF text and appendix; types appear only in dataset description and Table 8's aggregates) [LME]. Per-type evidence has to come from other papers:

| evidence | reader | finding | source |
|---|---|---|---|
| Zep Table 3 | gpt-4o-mini vs gpt-4o, same memory | SSU 92.9 both; **TR 54.1 vs 62.4, MS 47.4 vs 57.9**: the gap is only in date arithmetic and aggregation | [ZEP] |
| Hindsight Table 3 | OSS-20B vs OSS-120B vs Gemini-3 | gap grows with type difficulty: SSU 95.7 / 100 / 97.1, **TR 79.7 / 85.7 / 91.0, MS 79.7 / 81.2 / 87.2** | [HIND] |
| Mastra | gpt-4o vs gpt-5-mini | TR 85.7 -> 95.5, SSP 73.3 -> 100, SSA 82.1 -> 94.6; **MS tops out at 87.2 for every reader** ("a ceiling current systems hit") | [MAS] |
| Emergence | gpt-4o vs o3, full context | TR 60.2 -> 78.2, MS 47.4 -> 64.7 (their own gpt-4o full-context run vs o3); Oracle MS 80.5 -> 91.7 | [EMG] |
| MemForest (Qwen3-4B, S) | 4B readers | SSU 83-96, but **MS 42-65, TR 38-71** across systems (Mem0 TR 38.4, MS 42.1; Zep-local TR 54.9, MS 59.4); 4B -> 30B-A3B lifts MemForest 72.6 -> 81.8 but Zep-local only 69.6 -> 71.0 | [MF] |
| Survey, Qwen3.5-9B -> 27B | F1 | multi-session gains >= 1.6x for MemTree and MemoryOS; (LoCoMo) temporal gains > 10 F1 for Mem0/MemOS: "highly sensitive to the reasoning capability of the backbone" | [SURV] |
| LME Table 8 (Oracle, direct vs CoN) | small models | Qwen2.5-7B 0.282 -> 0.504, Llama-3.2-3B 0.522 -> 0.636, Phi-3.5-mini 0.488 -> 0.490, Mistral-Nemo 0.688 -> 0.686; Llama-3.1-8B 0.710 | [LME App. E.1] |
| LME error analysis (top-10, CoN, 8B) | Llama-8B | 52.6% correct retrieval + correct answer; **correct retrieval but wrong answer 18.8%** (GPT-4o 14.8%), i.e. 40-50% of all errors are reader errors; ~90% of correct answers need correct retrieval | [LME App. E.5] |

Question types that need multi-hop arithmetic or enumeration and that small readers fail (matches our R1 table: `cc6d1ec1` 3-1 months, `9aaed6a3` 1% of $75, `1192316e` 60+30 min, `7024f17c` 30 min -> 0.5 h, `gpt4_372c3eed` 2010 -> 2020, `0a995998` item count 2 vs 3):
- **Temporal-reasoning with interval math** ("how many days/weeks between", "how long had I been X when Y"): needs date subtraction across two memories with different anchors. Small readers also mis-state weekdays (R1: "05-18 is a Tuesday"). The official judge forgives off-by-one days, strict does not [LMEJ].
- **Multi-session counting/sums** ("how many ... in total", "all the items"): needs scanning every line and not dropping one; Mem0's prompt tells GPT-5 to do "a SECOND full scan" and notes items at positions 30-200 are missed even then [MEM0R]. A 9B drops items from a 15-line list (R1 0a995998: 2 of 3).
- **Knowledge-update with hedged values** (latest value vs earlier, "approximately 1300"): reasoning about "which replaces which" is where ADD-only systems lose points even with GPT-5 (Mem0 KU 93.6, the lowest non-MS cell) [MEM0B].
- **Preference questions**: score depends on the judge (rubric), not on memory; strict judge gives 0/3 for every reader we tried (R1).

Does any competitor show small-reader numbers? Yes, but only three sources, none of them the vendors:
1. Hindsight with GPT-OSS-20B (83.6, vs 39.0 full-context) [HIND] (P);
2. MemForest's third-party runs on Qwen3-4B / Gemma-12B (Mem0-OSS 44.8-59.2, Zep-Graphiti 69.6-75.6, best 72.6-78.4) with a different judge [MF] (P);
3. LME App. E.1 small models (Table 8) and the k=5 gpt-4o-mini comparison in [ZEN] (Letta 42.8, A-Mem 35.4, Mem0 31.8).
Mem0, Mastra, Supermemory, Zep, Letta, A-Mem, Memobase and LangMem publish **no** sub-GPT-4o-mini LME results themselves. Practical ceiling on S for 4B-30B readers across independent runs is roughly 70-82; on Oracle the 8B ceiling is 0.71-0.76 under the official GPT-4o judge [LME], i.e. a 9B at ~70-76 on an official-style judge would be at the published small-reader frontier, and the remaining 8-12 pts to GPT-4o Oracle are mostly TR/MS arithmetic.

## 8. Recommended order of work

1. Add the `lme_official` judge column (item 3) and recompute lenient-non-IDK on `lme_base2` so the comparison class is fixed. (minutes, no GPU beyond judging)
2. Run #1 (raw block, 2-4 arms) and #2 (order/grouping) on `lme_base2` plus det1/det2 as they finish; paired, reader-only.
3. Only if #1/#2 show net flips >= 4: #5 and #6 in `retrieval.rs`.
4. Keep ingest-side ideas (#9, `num_dedupe`, `verbatim_fallback`) behind the deterministic-mode check: compare det1 vs det2 first; if they differ by more than ~4 q the harness cannot see a +2 q lever.

Not worth building for this reader: session summaries, bigger k with a 9B, LLM time-range extraction, graph channel on (already shown to cost recall), agentic multi-call search (ASMR/Accumulator cost 11-15 calls or 112 s per question).

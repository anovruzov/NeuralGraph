# Paper framing memo — NeurIPS 2026 Agentic Web workshop (deadline Sep 5 AoE)

## 1. What the workshop rewards

CFP (projectnanda.org/workshops/neurips26): submissions should "treat the network — rather than a single model or agent — as the unit of analysis." "We welcome both early-stage work and more mature results, including position pieces, negative results, and datasets or benchmarks." Evaluation bullet: "Shared benchmarks for the adaptation, stability, and emergent capability of always-on collectives, where no standard exists today." T1 = "Algorithms for representing and discovering agent capabilities at web scale ... retrieval for discovery and resolution"; T2 includes "holistic evaluation of self-improving systems." Short paper 4 pp, double-blind, NeurIPS template, non-archival. OpenReview page needs login.

Honest fit: none of our work is about agent *networks*. Memory-of-one-agent is off-thrust unless framed as (i) shared/memory substrate for multi-agent (T1 representation) or (ii) evaluation integrity (T2). Best thrust: **T1 + evaluation**, sold as "the memory layer agents on the web will share, and why its benchmark is broken."

## 2. Candidate framings (ranked)

### #1 (recommended) "Signal, Not Structure: An Audited Negative Result on Graph Memory for Conversational Agents"
Abstract: Graph-structured memory is the default pitch of every agent-memory product (Mem0-graph, Zep/Graphiti, HippoRAG). We ask whether graph edges actually buy retrieval on LoCoMo (10 chats, 1540 QA) under a matched candidate budget. They do not: adding time-chain/same-speaker/dialogue-link neighbours to a flat top-50 raises recall@all by 1.4 pts; spending the same 30 slots on plain embedding back-fill raises it 4.1; mixing neighbours into the top-50 drops recall@50 from 48.0 to 32.9. Cheap *metadata signals* do help: a speaker-identity boost lifts single-hop recall@10 from 39.4 to 44.7. We also report an audit of our own prior 66.7% result, identifying three leakage channels (gold-answer confidence gating, gold-category routing, substring judge pass) and show the honest number. We release a leakage-free harness and argue the LoCoMo leaderboard (58–96% for the same systems) is an evaluation-protocol failure, not a capability spread.
Killer figure: recall@k curves (k=10..80), four lines: flat / flat+graph / flat+backfill / graph-mixed-in, all at identical candidate budget; inset bar: speaker boost on single-hop. Table: leakage ablation (original 66.7 → each channel removed → honest).
Evidence today: all retrieval-only numbers above (1540 q); audit facts; partial e2e single-hop (~68%, local judge). Needed by tomorrow: honest end-to-end 4-category number with one judge; H4/H6 retrieval deltas; ideally judge-sensitivity spread (GPT-4o lenient vs gemma vs substring on the same answers).
Main attack: "Your graph is a toy (3 edge types, regex entities); Graphiti/HippoRAG build entity-resolved KGs — the negative result doesn't transfer." Defence: scope it (cheap edges, budget-matched); Mem0's own paper shows graph +1.5 over base.

### #2 "Tesseract: Reversible Hierarchical Abstraction for Multi-Agent Memory" (position / blueprint)
Abstract: user→team→department→company abstraction with drill-down lineage; falsification plan (PDF E0–E10); NeuralGraph as leaf substrate.
Killer figure: PDF Fig. 1 plus a *pre-registered* capability-vs-N plot with empty axes and GO/KILL thresholds.
Evidence today: none for the hierarchy (PDF says "experimentally unproven"; nothing implemented). Needed: nothing more if sold as a pre-registration/position paper, which the CFP explicitly welcomes and which fits T1 best.
Main attack: "RAPTOR + model cascading + governed shared memory, no experiment." Second attack: the leaf-level system we'd cite just produced a negative result on graph structure, undercutting the blueprint's premise.

### #3 "Speaker Identity and Time as First-Class Signals in Agent Memory" (positive only)
Abstract: metadata-aware scoring beats structure. Killer figure: per-category recall gain from speaker boost + softer temporal penalty (H6) + keyword fix (H4).
Evidence today: speaker boost only (+5.3 recall@10 single-hop). Needed: H4, H6, temporal-category deltas, and an e2e accuracy gain.
Main attack: "one boost, one dataset, incremental; Zep already does bi-temporal + entity." Too thin alone; fold into #1 as Section 4.

Decision: write #1 with #3 as its positive half and one paragraph of #2 as outlook. Negative results + benchmark harness is literally in the CFP's welcome list.

## 3. Prior work and numbers

- LoCoMo (Maharana et al., ACL 2024): F1-scored, long-context/RAG baselines; we use its QA but replace F1 with audited LLM-judge and add budget-matched recall.
- Mem0 (arXiv 2504.19413, Apr 2025): reported Mem0 ~67 J, Mem0-graph ~68, Zep 65.99, full-context ~73. Sep 2026 mem0.ai/research now claims 92.5 (single-hop 94.6, multi-hop 95.4, temporal 82.3, ~6.9k tokens). We differ: we report where graph *fails* under matched budget and audit leakage.
- Zep/Graphiti: claimed 84% → Mem0 issue (getzep/zep-papers#5, May 2025) shows 58.44% after category-5 denominator fix; Zep rebuttal 75.14±0.17; marketing now 94.7. Independent Memori paper (arXiv 2603.19935): Memori 81.95 / Zep 79.09 / Mem0 62.47. Mem0's own May 2026 post: "None of these numbers were generated using the same model stack, judge model, or retrieval configuration." That sentence is our motivation slide.
- Others self-reported: ZeroMemory 96.1, ByteRover 92.2, Dakera 88.2, EverMemOS 93.05. Competing on accuracy is pointless; competing on protocol integrity is open.
- A-Mem (Zettelkasten links): 48.4 J in Mem0's table; we test whether link-following helps at all.
- MemGPT/Letta: OS-style paging, no graph; orthogonal.
- RAPTOR: tree summaries for retrieval — the Tesseract threat; we note hierarchy is future work.
- HippoRAG: PPR over KG, positive multi-hop gains; our contrast: without entity resolution, edge expansion under budget is net negative.
- GraphRAG (Microsoft): community summaries for global questions; LoCoMo is local-question dominated, which explains why structure buys little.

## 4. Four-page outline (recommended framing)

1. Intro (0.6 pp). Leaderboard spread 58–96 → protocol problem. Contributions: (i) budget-matched structure-vs-signal result, (ii) leakage taxonomy + honest rerun, (iii) open harness. Feeds from: audit, web numbers above.
2. System & harness (0.6 pp). What actually runs (AUDIT §1); three leakage channels A–C; recall@k = substring of gold parts (state loosely). Fig. 1: pipeline with leak points marked red.
3. Structure vs signal, retrieval-only (1.2 pp). Fig. 2 recall curves (Phase 0 flat/hybrid/graph on 282 single-hop + 1540 all). Table 1: +graph 1.4 / +backfill 4.1 / mixed-in 48.0→32.9. Para on H3 pair-nodes (structure at ingest, not query) and H4 keyword fix + H6 temporal (signal). Speaker boost 39.4→44.7.
4. End-to-end, honest (0.8 pp). Table 2: original 66.7 by category vs honest rerun, one local judge; judge-sensitivity row. H1 listwise rerank, H2 30-memory prompt, H5 open-domain keep-memories, H7 LLM router, H8 model swap each get one row (only if run on the same 100-id subset).
5. Discussion + outlook (0.6 pp). Why cheap edges fail (locality of LoCoMo questions, edge noise displaces hits); what would make structure pay (entity resolution, global questions); Tesseract hierarchy as pre-registered next step with GO/KILL thresholds. Limitations: one dataset, one system, small local models.
Appendix: prompts, leak code lines, per-category tables.

## 5. Five novelty boosts under one day

1. **Two-agent LoCoMo split**: give each speaker its own memory store; report the fraction of questions unanswerable from one agent's memory and recall when memories are federated vs pooled. Retrieval-only, makes the paper about *networked* memory (T1 fit).
2. **Speaker-swap probe**: rewrite each single-hop question with the other speaker's name; a memory system should drop, not stay flat. Measures whether identity is actually used (retrieval-only).
3. **Judge-sensitivity table**: same answers scored by GPT-4o-lenient, gemma-strict, substring; report spread. Explains leaderboard variance with data.
4. **Leakage checklist applied to public harnesses**: read Mem0/Zep/Memobase eval scripts for category-in-routing, gold-in-prompt, substring pass, cat-5 denominators; a 5-row yes/no table. Code reading only.
5. **Budget-matched recall as a protocol**: define recall@k@B (candidate budget B) and publish the harness as the paper's artifact; propose it as the "shared benchmark ... where no standard exists today."

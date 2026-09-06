# Overnight campaign (started 2026-09-05 06:15 local)

Bottleneck: one LM Studio server (google/gemma-4-e4b + nomic v1.5). LLM experiments are serialized.

Phase 0 (already running, main repo, sequential): flat -> hybrid -> graph, single_hop 282 q each.
Phase A (parallel now, worktrees, retrieval-only, no LLM):
  H3 pair-nodes (question+reply indexed together)
  H4 keyword formula fix + H6 softer temporal penalties
  P  paper framing memo (novelty, fit to Agentic Web CFP)
Phase B (sequential after Phase 0, worktrees, 100-question single_hop subset vs flat baseline on the same ids):
  H1 batched listwise reranker
  H2 30 memories in the answer prompt
  H5 keep memories in open-domain mode (open_domain subset)
  H7 LLM router
  H8 answer model swap to qwen3.6-35b-a3b (LAST: unloads Gemma)
Phase C: merge winners, full 1540 run, morning report.

## Phase C (designed 10:55 from Phase A/B lessons)
Lessons: retrieval decides (gold absent from final context 29% of single_hop; 88.7% correct when present); identity must come from metadata (per-agent routing +8.9 e2e); pair nodes are good back-fill, bad base (unpairing drags the other agent's message in); wider context hurts, router has no headroom, listwise rerank trades accuracy for speed.
  N1 smaller answer context (10 memories) + pointwise rerank              [LLM, 100 single_hop]
  N3 reply-only unpairing; pairs-as-base with reply-only; local+pairs as base   [retrieval-only, parallel now]
  N4 reranker sees the pair (previous message + candidate) instead of the candidate alone  [LLM, 100 single_hop]
  N5 judge sensitivity: rescore flat vs local_pairs answers with a STRICT judge prompt and with substring-only; report spread  [LLM, 2x282 calls]
  Capstone: full 1540-question run with the best config (RETRIEVAL_MODE=local_pairs + winners) for the paper's main table (~3.5 h)
Order on LM Studio: H5 -> H8 -> N1 -> N4 -> capstone -> N5.

# NeuralGraph repo audit (2026-09-05)

## What actually runs in the LoCoMo benchmark (demo/runner.py)

Ingest (per conversation, fresh InMemory storage):
  message -> nomic-embed-text embedding -> regex keywords/entities/dates -> NeuralNode(layer=MESSAGE)
  edges: ENTITY (same speaker, next 3 msgs), TEMPORAL (consecutive). ENTITY edges never read.
  DialogueLinker builds links, but runner.py:602-610 iterates tuples as ids -> silent no-op.
  Speaker profiles: one qwen call per message to extract facts.

Query:
  1. embed question (runner.py:579)
  2. Tesseract.retrieve top-50 (tesseract.py:1947): brute-force scan of all session nodes by 4 regex/embedding "stores"
     (Temporal 40, Entity 50, Reasoning 40, Adversarial 30), fused by query-type weights, min_charge cutoff 0.1
  3. dialogue-link expansion (broken, no-op)
  4. rerank with qwen2.5:7b, one call per candidate, score 0-3, charge += score*0.2 (reranker.py)
  5. top 15 (30 for LIST/AGGREGATION) into prompt, qwen2.5:7b answers, num_predict=60
  6. GPT-4o judges (lenient "generous" prompt), substring pre-pass

Routing: pure regex hashtables (temporal_utils.infer_query_mode + tesseract.detect_list_question_universal
+ service.is_temporal_question). No LLM router despite the commit message.

## Dead for the benchmark (never called)
retriever.py, flash_retriever.py, electron.py, pattern_completion.py, wavefront.py, interference.py,
hierarchy.py (EPISODE/TOPIC/PERSONA never created), consolidation.py, gating.py, temporal.py,
query_router.py, attribution.py, sqlite_storage.py, NeuralGraphService class in service.py,
all "biological" machinery (Hebbian/STDP/LTP/refractory). LSH index written but never queried.
Top-level __init__.py imports nonexistent `memmachine` package.

## Integrity problems (would sink the paper if a reviewer finds them)
A. Gold-answer peeking: runner.py:657 passes gold into query_single_hop; llm_profile_query.py:144-161
   computes confidence = overlap with gold; runner uses profile answer only if >= 0.5. Oracle best-of-two.
   Every used_speaker_profile=true row is contaminated.
B. Gold category label used for routing: runner.py:649, :684, :818 read qa["category"].
C. Judge substring pre-pass (runner.py:356) passes any answer containing the gold string.
D. Live OpenAI key hardcoded at runner.py:72, in git history on public GitHub.

## Bugs that cost accuracy (levers)
1. tesseract.py:141 regex \bwhat\s+\w+s\b matches "What is/does/was ..." -> most single_hop what-questions
   routed to LIST (comma-dump prompt, 30 memories) and excluded from profile path.
2. runner.py:602-610 dialogue-link expansion no-op (tuple vs id).
3. answering.py:21 num_predict=60 truncates list answers.
4. tesseract.py:1470 keyword_charge = matches / len(expanded_keywords); synonym expansion makes denominator
   hundreds so keyword signal ~0.
5. tesseract.py:1163-1171 temporal hard penalties (-1.0/-1.5) push correct memories under min_charge.
6. runner.py:647-648 temporal demotion to INFERENTIAL when two regex classifiers disagree.
7. runner.py:745 OPEN_DOMAIN_WORLD wipes all context; false positive = guaranteed miss.
8. runner.py:775-797 regex extractors (count, relationship status) overwrite LLM answer.
9. reranker.py:376 exceptions/timeouts silently score 1.
10. runner.py:497 created_at = datetime.now() for all nodes; recency decay keyed to wall clock.

## Harness facts
- python demo/runner.py, no args, no subset support. Needs Ollama (qwen2.5:7b-instruct, nomic-embed-text).
- 1540 questions (cat 5 adversarial skipped). ~2.4 s/question after ingest. Full run ~1 h + ingest.
- Output omega.json. All analysis scripts in demo/ point at files that do not exist.
- recall@k = substring match of gold parts in memory text (loose).
- No requirements.txt. Deps: aiohttp, numpy.
- Baseline (maximal.json, 2025-12-20): 66.7% overall; single_hop 52.1, temporal 64.5, open_domain 52.1, multi_hop 74.1.

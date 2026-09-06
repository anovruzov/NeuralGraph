# NeuralGraph: research campaign notes (Sept 5-6, 2026)

Written for the co-author. Everything here is in this branch. Start with `docs/research/REPORT.md`.

## 1. What this branch contains

| Area | Files | What changed |
|---|---|---|
| LLM backend | `NeuralGraph/llm_backend.py` | One shim for LM Studio (OpenAI-compatible) or Ollama. Env: `LLM_BASE_URL`, `LLM_MODEL`, `EMBED_MODEL`, `ANSWER_MODEL`, `LLM_REASONING_EFFORT` (default `none`, disables Gemma/Qwen thinking). All five call sites (`answering.py`, `reranker.py`, `llm_profile_extractor.py`, `llm_profile_query.py`, `demo/runner.py`) go through it. |
| Benchmark harness | `demo/runner.py` | Leakage removed (gold answer no longer gates the speaker-profile path; gold category label no longer drives routing). OpenAI key removed (reads `OPENAI_API_KEY`; local judge fallback). Env run controls: `RUN_NAME`, `ONLY_CAT`, `ONLY_CONV`, `MAX_QUESTIONS`, `RETRIEVAL_MODE` (`flat` / `hybrid` / `graph` / `local_pairs` / `mega`), open-domain flags `OPEN_DOMAIN_KEEP_CONTEXT`, `OPEN_DOMAIN_FORCE_INFER`, `OPEN_DOMAIN_INFER_WORLD`. Results go to `demo/results/<RUN_NAME>.json`. |
| Retrieval | `NeuralGraph/tesseract.py` | LIST-question regex fix (`what is/does/was` no longer routed as a list). |
| New retrievers | `NeuralGraph/mega_search.py`, `NeuralGraph/kg_extractor.py` | Vector + BM25 + entity-graph PageRank hop fused by reciprocal rank (mega search); LLM-built entity graph with alias resolution (kg_extractor, run `python -m NeuralGraph.kg_extractor`). |
| Fast evaluators | `demo/retrieval_eval.py`, `demo/two_agent_eval.py` | Retrieval-only recall@10/50/all, no LLM calls, about 4 min for all 1,540 questions. Embeddings cached under `demo/results/emb_cache/` (not committed, 321 MB; regenerates on first run). |
| Reports | `docs/research/` | `REPORT.md` (final), `AUDIT.md` (what actually runs vs dead code), `P_paper_framing.md` (venue fit, framings, prior work), one file per experiment (`H1`..`H9`, `N1`, `N3`, `N4`, `N5`). |
| Results | `demo/results/*.json` | Per-question outputs for the flat / hybrid / graph / local_pairs single_hop runs, the 584-question capstone, and the mega-search runs. |

## 2. Headline numbers (leakage-free, local judge; see REPORT.md sections 1, 4, 5 for the four-judge table)

- single_hop, all 282 questions: original flat retrieval 64.9% -> per-agent routing + pair back-fill 73.8% (+8.9; holds under all four judges).
- Conversations 1-4 (584 q): 73.8% overall vs 68.0% for the old leaky Dec-2025 run on the same ids (different judge, so indicative only).
- Same answers score 13.5 / 21.6 / 51.4 / 64.9% depending only on the grader. Report judge model and prompt with every number.

## 3. What worked and what did not (one line each)

Worked: per-agent memory routing (route the question to the named speaker's own store); question+reply pair nodes as back-fill.
Null or negative: graph-neighbour expansion, listwise rerank (faster, less accurate), context 5/10/30/50, LLM router, keyword-formula fix, softer temporal penalties, reply-only pairs, pair-aware rerank, bigger answer model, mega search end to end (better recall@10, worse answers). Details in `docs/research/REPORT.md` section 3.

## 4. How to run

```bash
# one-time
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python aiohttp numpy rank-bm25 pytest
# LM Studio at http://127.0.0.1:1234 with google/gemma-4-e4b and text-embedding-nomic-embed-text-v1.5 loaded
# (or Ollama: LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text)

# retrieval-only comparison, ~4 min, no LLM
EMB_CACHE_DIR=demo/results/emb_cache MODE=combined .venv/bin/python demo/two_agent_eval.py

# end-to-end, best shipped config, single_hop only (~40 min)
cd demo && RUN_NAME=my_run ONLY_CAT=single_hop RETRIEVAL_MODE=local_pairs ../.venv/bin/python -u runner.py

# full benchmark, best config (~3.5 h); conversations 5-10 are the ones not yet run
cd demo && RUN_NAME=capstone_rest ONLY_CONV=4,5,6,7,8,9 RETRIEVAL_MODE=local_pairs \
  OPEN_DOMAIN_KEEP_CONTEXT=1 OPEN_DOMAIN_FORCE_INFER=2 OPEN_DOMAIN_INFER_WORLD=1 ../.venv/bin/python -u runner.py

# rescore any result file under strict / lenient / substring judges (~10 min)
.venv/bin/python docs/research/n5_rescore.py   # edit FILES at the top
```

## 5. Security

`demo/runner.py` in earlier commits on `main` contains a live OpenAI API key (commit history, line 72). It is removed in this branch, but it remains in git history and must be revoked at platform.openai.com. Rewriting history is a separate decision.

## 6. In progress at the time of writing

LLM-built entity graph (`kg_extractor.py`) extraction for all 10 conversations, then a retrieval and end-to-end comparison of the KG-backed graph hop vs the shipped `local_pairs` stack; then the full-benchmark run of whichever wins.

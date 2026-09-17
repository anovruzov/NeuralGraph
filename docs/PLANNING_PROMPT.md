# Planning brief: next phase of NeuralGraph cross-chat memory

You are a planning agent. Produce a plan, not code. Read the repository first, then write the plan
described under **Deliverable**. Do not modify files.

## Where things are

Repository `anovruzov/NeuralGraph`, branch `claude/chat-memory-storage-iglf1w` (commits on top of `main`
from "Add cross-chat memory: SQLite store, parallel Qwen worker, hybrid retrieval, dashboard, MCP" onward).

Read in this order:

1. `docs/CHAT_MEMORY.md` — architecture, worker pipeline, retrieval, dashboard, REST, MCP, config knobs,
   tests, deployment, known limits.
2. `NeuralGraph/chat_memory/` — the package:
   `store.py` (SQLite, job queue, atomic fenced `apply_plan`), `extraction.py` (prompts, gate, entity
   registry, grounding, reconciliation), `worker.py` (parallel loops, retries, maintenance), `retrieval.py`
   (vector + FTS + entity-graph channels, RRF, incremental index), `service.py` (`ChatMemory` facade, token
   ledger, five-axis grade), `mcp_server.py` (stdio + Streamable HTTP), `ui/server.py` + `ui/dashboard.html`,
   `cli.py`, `testing.py` (deterministic fake model), `llm.py`, `models.py`, `textutil.py`, `jsonutil.py`.
3. `NeuralGraph/tests/test_chat_memory_*.py` — 106 tests; `test_chat_memory_review_fixes.py` pins the
   defects found by an adversarial review (grounding, queue fencing, supersede race, CSRF, filters, …).
4. `demo/chat_memory_live_demo.py`, `demo/chat_memory_demo.py`, `evaluation/chats/sample_chats.jsonl`.
5. `docs/research/REPORT.md` — the LoCoMo campaign this design borrows from (per-speaker routing, RRF,
   candidate precision vs recall, judge sensitivity) and `demo/runner.py` (the LoCoMo harness).

Run the suite: `.venv/bin/python -m unittest NeuralGraph.tests.test_chat_memory_store
NeuralGraph.tests.test_chat_memory_extraction NeuralGraph.tests.test_chat_memory_worker
NeuralGraph.tests.test_chat_memory_retrieval NeuralGraph.tests.test_chat_memory_mcp
NeuralGraph.tests.test_chat_memory_review_fixes` (pytest is broken repo-wide by the stale root
`__init__.py`; that is a known, separate issue).

## State of play (facts)

- Everything above works end to end **with the deterministic fake model only**. No run against a real
  Qwen/LM Studio model has happened. The prompts (`MEMORY_PROMPT`, `RELATION_PROMPT`, `RECONCILE_PROMPT`),
  the cosine thresholds (`dedupe_cosine` 0.94, `reconcile_cosine` 0.62, `related_cosine` 0.55,
  `vector_min_sim` 0.05, `context_min_vector_sim` 0.3, `merge_cosine` 0.96) and the grounding thresholds
  (`min_grounding_overlap` 0.2) are untuned.
- There is no labelled evaluation set for "our chats" (user ↔ assistant transcripts): no ground truth for
  which memories should be extracted, which reconcile decision is right, or which memory answers a question.
- A design panel produced a much larger spec (two-phase extract/reconcile with persisted candidates,
  "disputed" contradictions, per-speaker entity scoping with unmerge, bitemporal validity, OAuth for
  claude.ai connectors). It was deliberately **not** adopted wholesale; the shipped design is inline
  reconciliation with newest-observation-wins supersession and history links.

## Known gaps and limits (start your analysis here)

1. **Real-model validation** — prompts, JSON reliability, thresholds, latency/throughput with Ollama
   (`OLLAMA_NUM_PARALLEL`) vs LM Studio; cost per message; how often reconcile gets UPDATE/CONTRADICT wrong.
2. **No evaluation harness** — nothing measures extraction precision/recall, reconcile accuracy, or
   cross-chat QA. LoCoMo can be re-used: ingest the 10 conversations as chats (`ingest --locomo`), answer
   the QA pairs from *memories* (`memory_search`/`context_for`) and compare with the raw-message numbers in
   `docs/research/REPORT.md` under the same judges.
3. **Identity** — entities are keyed by display name; two different people called "Sam" in different
   chats merge; no entity scoping, no merge/unmerge tooling; speaker identity in multi-user deployments
   relies on the caller passing stable ids.
4. **Contradictions** — newest observation wins and supersedes; a wrong CONTRADICT from a 7B model hides a
   true fact (recoverable through history, but not surfaced). No "disputed" state, no user confirmation loop.
5. **Retrieval quality** — channel weights, priors and the relative cutoff in `context_for` are guesses; no
   reranker; no per-chat cap or near-duplicate suppression in results; the graph channel is one hop.
6. **Concurrency model** — one worker process per database; per-chat ordering only (same-subject
   candidates from two chats reconcile concurrently and rely on commit-time reconciliation + maintenance
   merge); no multi-process worker coordination beyond SQLite leases.
7. **Operations** — schema version 1 with no migration path; no export/import/backup commands; no
   metrics endpoint; static bearer tokens only (claude.ai custom connectors expect OAuth); no rate limiting;
   audit log is the only observability besides the dashboard.
8. **Product surface** — dashboard has no memory editing/forgetting UI, no graph visualisation, no per-user
   views; assistant turns are context-only by default (commitments the assistant makes are not remembered);
   the token ledger is a chars/4 estimate.
9. **Repo hygiene** — root `__init__.py` breaks pytest; no CI runs the unittest suite; no packaging.

## Deliverable

Write `docs/CHAT_MEMORY_PLAN.md` containing:

1. **Phased plan** (suggested arc: validate → measure → tune → harden → extend), each phase with: goal,
   concrete tasks naming the files/functions to change, acceptance criteria with numbers where possible,
   risks, rough effort (S/M/L), dependencies on earlier phases.
2. **Evaluation protocol** for extraction and cross-chat retrieval, including how to build a small labelled
   set from `evaluation/chats/sample_chats.jsonl` + real transcripts, which judges to use (see the
   judge-sensitivity results in `docs/research/REPORT.md` §4), and the LoCoMo-as-chats comparison.
3. **Threshold tuning procedure** for the cosine/grounding knobs against nomic-embed-text and one other
   embedding model, with a script outline.
4. **Decision list** — items that need the owner's input (identity scoping, disputed vs supersede, OAuth,
   whether to adopt parts of the panel spec), each with options, trade-offs and your recommendation.
5. **Prioritised backlog** (top 15) with a one-line rationale each.

Constraints: keep the system local-first and dependency-light (stdlib + aiohttp + numpy + rank_bm25 +
optional `mcp`), keep the unittest suite deterministic and fake-model based for CI, do not touch
`NeuralGraph/coordination/` or the LoCoMo benchmark harness, and prefer additive changes over rewrites of
`store.py`/`extraction.py`. Be concrete: file paths, function names, numbers. Prefer fewer, sharper tasks
over an exhaustive list.

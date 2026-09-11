# NeuralGraph

**A graph-native memory and retrieval system for long-running AI agents.**

NeuralGraph turns conversations and events into structured, searchable memory. Instead of treating memory as a flat vector store, it combines semantic retrieval, graph relationships, speaker identity, temporal reasoning, reranking, and evidence attribution to recover the right context for an answer.

## Why NeuralGraph

Most agent-memory systems are good at finding text that looks similar to a query. They are much weaker at questions that require identity, chronology, aggregation, or multi-step reasoning.

NeuralGraph is designed for those harder cases:

- **Who said what?** Speaker-aware indexing and retrieval
- **When did it happen?** Relative-date resolution and temporal query expansion
- **What changed over time?** Chronological memory organization
- **What facts belong together?** Graph-based links between related memories
- **Why was this answer produced?** Retrieval-stage metrics, score breakdowns, and evidence tracking
- **What should happen when memory is insufficient?** Query routing between strict retrieval, inference, and open-domain fallback

## Core Capabilities

### Graph-native memory
Memories are represented as nodes with metadata and relationships rather than isolated text chunks. This allows the system to preserve identity, sequence, provenance, and semantic connections.

### Hybrid retrieval
NeuralGraph combines multiple signals instead of relying on embedding similarity alone:

- semantic similarity
- entity overlap
- keyword matching
- temporal relevance
- speaker-aware boosts
- specificity bonuses
- reranking deltas

### Temporal reasoning
The system resolves relative expressions such as `yesterday`, `last week`, and explicit dates against the original message timestamp. It can expand time-sensitive queries and preserve the correct granularity of an event.

### Query routing
Questions are routed into specialized answer modes, including:

- strict fact extraction
- temporal reasoning
- list questions
- aggregation
- memory-grounded inference
- open-domain fallback

### Retrieval attribution
NeuralGraph records what happened at each retrieval stage, including candidate counts, latency, score components, reranking effects, and the evidence supplied to the final answer.

## Architecture

```text
Conversation / Event Stream
            │
            ▼
  Preprocessing + Metadata
  - speaker identity
  - entities
  - timestamps
  - resolved relative dates
            │
            ▼
      Neural Memory Graph
  - memory nodes
  - semantic links
  - temporal links
  - provenance
            │
            ▼
       Hybrid Retrieval
  - vector search
  - entity and keyword signals
  - temporal scoring
  - speaker-aware reranking
            │
            ▼
        Query Router
  - strict
  - temporal
  - list
  - aggregation
  - inference
            │
            ▼
   Grounded Answer + Evidence
```

## Process Summary

End-to-end path a message takes from ingestion to a grounded answer.

| # | Stage | Input | What happens | Key modules | Output |
|---|-------|-------|--------------|-------------|--------|
| 1 | Episode intake | Raw conversation / event stream | Extract text, speaker, timestamp and entities; resolve relative dates against the message timestamp | `service.py`, `temporal_utils.py` | Normalized episode with metadata |
| 2 | Node creation | Normalized episode | Embed the text, compute wave amplitudes, write a Layer-0 message node | `service.py`, `data_types.py`, `storage.py` / `sqlite_storage.py` | Message node (Layer 0) |
| 3 | Edge building | Message node | Add TEMPORAL and ENTITY edges, then batched SEMANTIC edges above the similarity threshold | `temporal.py`, `hierarchy.py` | Linked memory graph |
| 4 | Dialogue linking | Linked graph | Bind question/answer pairs, topic threads and coreference across messages | `dialogue_linker.py` | Q/A pair and thread edges |
| 5 | Consolidation | Linked graph | Deduplicate, prune weak edges, promote and merge nodes across the 4-layer hierarchy | `consolidation.py`, `gating.py` | Compressed, promoted memory |
| 6 | Query routing | User question | Classify query type, extract entities, pick a speaker filter and retrieval strategy (strict / temporal / list / aggregation / inference / open-domain) | `query_router.py`, `tesseract.py` | Query analysis + strategy |
| 7 | Candidate filtering | Query analysis | Boost-mode filtering so entity and speaker matches are favored rather than hard-excluded | `retriever.py` (filtered retriever) | Boosted candidate set |
| 8 | Multi-stage recall | Candidate set | Stage 1 fast recall (vector + keyword + wave routing), Stage 2 entity expansion (3 hops), Stage 3 temporal chain (3 hops), Stage 4 hierarchy traversal (2 hops), Stage 5 co-activation (1 hop) | `retriever.py`, `lsh.py`, `flash_retriever.py`, `mega_search.py` | Recalled node set |
| 9 | Advanced processing | Recalled nodes | CA3 pattern completion, wavefront propagation and interference detection | `pattern_completion.py`, `wavefront.py`, `interference.py` | Completed, disambiguated set |
| 10 | Reranking | Completed set | Multi-signal scoring: base score, keyword match, entity overlap, heat, importance, wave alignment | `reranker.py` | Ranked evidence |
| 11 | Post-processing | Ranked evidence | Dialogue-link expansion, STDP updates, co-activation recording (the graph learns from the retrieval) | `dialogue_linker.py`, `gating.py` | Final context + updated weights |
| 12 | Answering | Final context | Route-specific prompting over the retrieved evidence, with open-domain fallback when memory is insufficient | `answering.py`, `prompts.py`, `external_retriever.py`, `llm_backend.py` | Grounded answer |
| 13 | Attribution | Every stage above | Record candidate counts, latency, score components and rerank deltas per stage | `attribution.py` | Per-stage retrieval trace |
| 14 | Evaluation | Benchmark questions | Leakage-free harness runs the pipeline end to end and scores answers under multiple judges | `demo/runner.py`, `demo/retrieval_eval.py`, `demo/two_agent_eval.py` | Benchmark results in `demo/results/` |

## Local Models

The current answering and embedding pipeline is designed to work with local models through [Ollama](https://ollama.com/).

Default configuration:

- Answer model: `qwen2.5:7b-instruct`
- Embedding model: `nomic-embed-text`
- Ollama endpoint: `http://localhost:11434`

Example model setup:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
ollama serve
```

## Example Use Cases

- persistent memory for autonomous agents
- personal AI that remembers conversations accurately
- multi-agent shared memory
- timeline and event reconstruction
- long-horizon research assistants
- benchmarkable retrieval-augmented generation
- explainable memory retrieval

## Design Principles

1. **Memory should preserve structure, not just text.**
2. **Retrieval should use multiple signals.**
3. **Time and speaker identity are first-class metadata.**
4. **Answers should remain grounded in traceable evidence.**
5. **The system should run locally when possible.**
6. **Every retrieval failure should be measurable.**

## Current Status

NeuralGraph is under active development. Current work focuses on improving single-hop extraction, temporal questions, list and aggregation queries, query routing, latency tracking, and benchmark attribution.

## Roadmap

- [ ] publish reproducible benchmark results
- [ ] add a minimal quick-start example
- [ ] expose the memory graph through a documented API
- [ ] add graph visualization tools
- [ ] support additional local and hosted model providers
- [ ] add multi-agent memory namespaces and permissions
- [ ] package the core library for easier installation

## Authors

| ID | Name |
|----|------|
| msn782 | Muhammad-Ali Novruzov |
| aa224487 | Amlan Abhidarshi |

Built by [Muhammad-Ali Novruzov](https://github.com/anovruzov) and Amlan Abhidarshi as part of ongoing work on persistent memory, agent orchestration, and graph-based intelligence.

## License

No license has been selected yet. All rights are reserved until a license is added.

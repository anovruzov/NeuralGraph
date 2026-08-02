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

## Model Configuration

The benchmark uses GPT through OpenAI's Responses API. Retrieval
embeddings remain local and deterministic, so only constrained prompts and
retrieved context are sent to the hosted model.

Default configuration:

- Answer model: `gpt-5.6-sol`
- Reranker/profile/judge model: `gpt-5.6-terra`
- Embedding model: local 1,024-dimensional signed feature hashing

Configure the API key privately in your shell or secret manager:

```bash
export OPENAI_API_KEY="..."
```

Never put the key in source code, committed environment files, or benchmark
output.

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

## Reproducible LoCoMo Evaluation

The benchmark runner evaluates all five QA categories, ingests LoCoMo's
provided image captions, and keeps dataset labels out of retrieval and answer
generation. Retrieval recall is measured against evidence IDs rather than by
searching for gold-answer text. Reports distinguish any-evidence recall,
complete-evidence recall, fractional evidence coverage, and the evidence that
actually survives into the answer context; the old any-hit number alone can
substantially overstate multi-hop readiness.

```bash
python -m pip install -r requirements.txt
export OPENAI_API_KEY="..."
python demo/runner.py
```

Useful local-only controls:

```bash
NEURALGRAPH_MAX_QUESTIONS=100 python demo/runner.py
NEURALGRAPH_ANSWER_MODEL=gpt-5.6-sol python demo/runner.py
NEURALGRAPH_RERANKER_MODEL=gpt-5.6-terra python demo/runner.py
NEURALGRAPH_USE_RERANKER=0 python demo/runner.py
```

The runner fails closed when `OPENAI_API_KEY` is missing rather than silently
producing incomplete or incomparable scores.

## Roadmap

- [ ] publish reproducible benchmark results
- [ ] add a minimal quick-start example
- [ ] expose the memory graph through a documented API
- [ ] add graph visualization tools
- [ ] support additional local and hosted model providers
- [ ] add multi-agent memory namespaces and permissions
- [ ] package the core library for easier installation

## Author

Built by [Ali Novruzov](https://github.com/anovruzov) as part of ongoing work on persistent memory, agent orchestration, and graph-based intelligence.

## License

No license has been selected yet. All rights are reserved until a license is added.

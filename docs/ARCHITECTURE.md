# MemMachine System Architecture

## Latest Benchmark Results (LoCoMo-10)

| Run | Date | Overall Accuracy | Model |
|-----|------|------------------|-------|
| **PARALLEL v1.0** | 2024-12-06 | **70.34%** | qwen2.5:7b-instruct |
| PARALLEL v1.0 | 2024-12-07 | 67.82% | qwen2.5:7b-instruct |
| IMPROVED | 2024-12-06 | 61.57% | qwen2.5:7b-instruct |

---

## System Architecture Diagram

```
+==============================================================================+
|                           MEMMACHINE ARCHITECTURE                            |
|                     Cognitive Memory System for LLM Agents                   |
+==============================================================================+

                              +-------------------+
                              |   Client/Agent    |
                              |  (REST/MCP/SDK)   |
                              +--------+----------+
                                       |
                                       v
+------------------------------------------------------------------------------+
|                              API LAYER                                        |
|  +----------------+  +------------------+  +------------------+               |
|  |  REST API v2   |  |   MCP (HTTP)     |  |   MCP (STDIO)    |               |
|  |  (FastAPI)     |  |   Server         |  |   Server         |               |
|  +----------------+  +------------------+  +------------------+               |
+------------------------------------------------------------------------------+
                                       |
                                       v
+------------------------------------------------------------------------------+
|                         EPISODIC MEMORY MANAGER                               |
|  +------------------------------------------------------------------------+  |
|  |                        EpisodicMemory Instance                         |  |
|  |  (Per-session orchestrator for memory operations)                      |  |
|  +------------------------------------------------------------------------+  |
+------------------------------------------------------------------------------+
          |                    |                    |                    |
          v                    v                    v                    v
+------------------+  +------------------+  +------------------+  +------------------+
|  INGESTION       |  |  COGNITIVE       |  |  RETRIEVAL       |  |  KNOWLEDGE       |
|  PIPELINE        |  |  PROCESSING      |  |  PIPELINE        |  |  GRAPH           |
+------------------+  +------------------+  +------------------+  +------------------+
          |                    |                    |                    |
          v                    v                    v                    v
+==============================================================================+
|                     COGNITIVE PROCESSING MODULES (6 Phases)                  |
+==============================================================================+

  Phase 1              Phase 2              Phase 3              Phase 4
+---------------+   +---------------+   +---------------+   +---------------+
| PRIVACY       |   | EMOTIONAL     |   | MID-TERM      |   | KNOWLEDGE     |
| SANITIZER     |   | ENCODER       |   | MEMORY        |   | GRAPH (MeKB)  |
+---------------+   +---------------+   +---------------+   +---------------+
| - PII Detect  |   | - Emotion     |   | - Segment     |   | - Entity      |
| - Sensitivity |   |   Detection   |   |   Buffering   |   |   Extraction  |
| - Compliance  |   | - Mood Track  |   | - Promotion   |   | - Relation    |
|   Auditing    |   | - Resonance   |   |   to LTM      |   |   Mapping     |
| - Segment     |   |   Scoring     |   | - Temporal    |   | - Reasoning   |
|   Folding     |   |               |   |   Windows     |   |   Queries     |
+---------------+   +---------------+   +---------------+   +---------------+

  Phase 5              Phase 6
+---------------+   +---------------+
| GRANULARITY   |   | DOMAIN        |
| ROUTER        |   | CLASSIFIER    |
| (MemGAS)      |   | (Sharding)    |
+---------------+   +---------------+
| - Query       |   | - Content     |
|   Entropy     |   |   Classification|
| - Adaptive    |   | - Shard       |
|   Retrieval   |   |   Routing     |
| - Level       |   | - Multi-shard |
|   Selection   |   |   Fallback    |
+---------------+   +---------------+

+==============================================================================+
|                         THREE-TIER MEMORY HIERARCHY                          |
+==============================================================================+

+------------------+     +------------------+     +------------------+
|   SHORT-TERM     | --> |   MID-TERM       | --> |   LONG-TERM      |
|   MEMORY (STM)   |     |   MEMORY (MTM)   |     |   MEMORY (LTM)   |
+------------------+     +------------------+     +------------------+
|                  |     |                  |     |                  |
| - Session buffer |     | - Segment-based  |     | - Declarative    |
| - Recent context |     | - Temporal       |     |   memories       |
| - Fast access    |     |   windowing      |     | - Vector search  |
| - In-memory      |     | - Promotion      |     | - Persistent     |
|                  |     |   scoring        |     |   storage        |
+------------------+     +------------------+     +------------------+
         |                       |                       |
         +-----------------------+-----------------------+
                                 |
                                 v
+==============================================================================+
|                           COMMON COMPONENTS                                   |
+==============================================================================+

+------------------------+  +------------------------+  +------------------------+
|      EMBEDDERS         |  |   LANGUAGE MODELS      |  |      RERANKERS         |
+------------------------+  +------------------------+  +------------------------+
| - OpenAI Embedder      |  | - OpenAI Chat          |  | - BM25 Reranker        |
| - Amazon Bedrock       |  | - OpenAI Responses     |  | - Cross-Encoder        |
| - Sentence Transformer |  | - Amazon Bedrock       |  | - RRF Hybrid           |
| - Ollama (nomic-embed) |  | - Ollama (qwen2.5)     |  | - Embedder Reranker    |
+------------------------+  +------------------------+  +------------------------+

+------------------------+  +------------------------+  +------------------------+
|    EPISODE STORE       |  |   VECTOR/GRAPH STORE   |  |   RESOURCE MANAGERS    |
+------------------------+  +------------------------+  +------------------------+
| - SQLAlchemy Store     |  | - Neo4j Vector Store   |  | - Database Manager     |
| - Count Caching Store  |  | - PGVector Support     |  | - Embedder Manager     |
| - Episode Model        |  | - Graph Relationships  |  | - LLM Manager          |
| - Session Manager      |  | - Semantic Storage     |  | - Reranker Manager     |
+------------------------+  +------------------------+  +------------------------+

+------------------------+  +------------------------+
|  TEMPORAL NORMALIZER   |  |    METRICS FACTORY     |
+------------------------+  +------------------------+
| - Relative date        |  | - Prometheus Metrics   |
|   resolution           |  | - Latency tracking     |
| - Session timestamp    |  | - Counter metrics      |
|   context              |  | - Summary metrics      |
+------------------------+  +------------------------+

+==============================================================================+
|                           DATA FLOW DIAGRAM                                   |
+==============================================================================+

  INGESTION FLOW:
  ---------------

  Input Episode
       |
       v
  +--------------------+
  | Privacy Sanitizer  |-----> [BLOCKED if critical PII]
  +--------------------+
       |
       v
  +--------------------+
  | Emotional Encoder  |-----> [emotions, mood state]
  +--------------------+
       |
       v
  +--------------------+
  | Domain Classifier  |-----> [domain shard assignment]
  +--------------------+
       |
       +------------------+------------------+
       |                  |                  |
       v                  v                  v
  +----------+      +-----------+      +-----------+
  |   STM    |      |    MTM    |      |    LTM    |
  +----------+      +-----------+      +-----------+
                          |
                          v
                    +-----------+
                    |    KG     | (Entity/Relation extraction)
                    +-----------+


  RETRIEVAL FLOW:
  ----------------

  Query
    |
    v
  +----------------------+
  | Granularity Router   |-----> [entropy analysis, level selection]
  +----------------------+
    |
    v
  +----------------------+
  | Domain Classifier    |-----> [shard routing]
  +----------------------+
    |
    +------------------+------------------+
    |                  |                  |
    v                  v                  v
  +----------+      +-----------+      +-----------+
  |   STM    |      |    MTM    |      |    LTM    |
  | (search) |      |  (search) |      |  (search) |
  +----------+      +-----------+      +-----------+
    |                  |                  |
    +------------------+------------------+
    |
    v
  +----------------------+
  | Reranker (Hybrid)    |-----> [BM25 + Semantic fusion]
  +----------------------+
    |
    v
  +----------------------+
  | Knowledge Graph      |-----> [reasoning augmentation]
  +----------------------+
    |
    v
  [Ranked Episodes with Context]

+==============================================================================+
|                         BENCHMARK ARCHITECTURE                                |
+==============================================================================+

  +-----------------+     +-------------------+     +------------------+
  | LoCoMo Dataset  | --> | Benchmark Runner  | --> | Results Analyzer |
  | (locomo10.json) |     | (Parallel/Domain) |     | (JSON/CSV)       |
  +-----------------+     +-------------------+     +------------------+
                                   |
                +------------------+------------------+
                |                  |                  |
                v                  v                  v
        +---------------+  +---------------+  +---------------+
        | Ingestion     |  | Question      |  | LLM Judge     |
        | Pipeline      |  | Answering     |  | (Qwen/GPT-4)  |
        +---------------+  +---------------+  +---------------+

  Category Metrics:
  - single_hop:   Direct fact retrieval
  - temporal:     Date/time reasoning
  - open_domain:  Inference/synthesis
  - multi_hop:    Multi-fact reasoning
  - adversarial:  Entity confusion tests

+==============================================================================+
```

## Module Dependency Tree

```
memmachine/
├── main/
│   └── memmachine.py                 # Main entry point
├── server/
│   ├── app.py                        # FastAPI application
│   ├── mcp_http.py                   # MCP HTTP server
│   ├── mcp_stdio.py                  # MCP STDIO server
│   └── api_v2/                       # REST API v2
├── episodic_memory/
│   ├── episodic_memory.py            # Core orchestrator
│   ├── short_term_memory/            # STM implementation
│   ├── mid_term_memory/              # MTM with segmentation
│   ├── long_term_memory/             # LTM with vector search
│   └── declarative_memory/           # Data types
├── semantic_memory/
│   ├── semantic_memory.py            # Semantic operations
│   ├── semantic_llm.py               # LLM integration
│   └── storage/                      # Neo4j/PGVector backends
├── knowledge_graph/                  # Phase 4: MeKB
│   ├── entity_extractor.py           # Named entity recognition
│   ├── relation_mapper.py            # Relationship extraction
│   └── reasoning_engine.py           # Graph-based reasoning
├── sharded_memory/                   # Domain-based sharding
│   └── shard_manager.py              # Shard lifecycle
└── common/
    ├── domain_classifier/            # Phase 6: Memory sharding
    ├── granularity_router/           # Phase 5: MemGAS
    ├── privacy_sanitizer/            # Phase 1: PII handling
    ├── emotional_encoder/            # Phase 2: Emotion analysis
    ├── temporal_normalizer.py        # Date/time normalization
    ├── embedder/                     # Vector embeddings
    ├── language_model/               # LLM interfaces
    ├── reranker/                     # Hybrid reranking
    ├── episode_store/                # Persistence layer
    ├── vector_graph_store/           # Neo4j integration
    └── resource_manager/             # DI container
```

## Key Performance Characteristics

| Component | Latency | Purpose |
|-----------|---------|---------|
| STM Lookup | ~1ms | Recent context |
| MTM Search | ~10ms | Temporal segments |
| LTM Vector Search | ~50ms | Semantic similarity |
| BM25 Rerank | ~5ms | Keyword boosting |
| KG Reasoning | ~100ms | Entity relationships |
| LLM Generation | ~2-5s | Answer synthesis |

## Configuration Options

```yaml
episodic_memory:
  privacy_enabled: true
  emotional_encoding_enabled: true
  mid_term_memory_enabled: true
  knowledge_graph_enabled: true
  granularity_routing_enabled: true
  domain_classification_enabled: true

benchmarks:
  ollama_model: "qwen2.5:7b-instruct"
  judge_model: "deepseek-r1:14b"
  embedding_model: "nomic-embed-text"
  top_k_retrieval: 30
  context_limit: 12000
```

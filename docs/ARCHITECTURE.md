# MemMachine System Architecture

> A Unified Memory Layer for AI Agents with Bio-Inspired Neural Architecture

## Latest Benchmark Results (LoCoMo-10)

| Run | Date | Overall Accuracy | Model |
|-----|------|------------------|-------|
| **TESSERACT 4D** | 2024-12-09 | **85%+** | qwen2.5:7b-instruct |
| **PARALLEL v1.0** | 2024-12-06 | **70.34%** | qwen2.5:7b-instruct |
| HYBRID | 2024-12-08 | 67.82% | qwen2.5:7b-instruct |
| BASELINE | 2024-12-06 | 61.57% | qwen2.5:7b-instruct |

---

## Executive Summary

MemMachine is an open-source, unified memory layer for AI agents that enables persistent memory across sessions, users, and LLMs. It implements a sophisticated multi-layered memory architecture combining:

- **Episodic Memory** (conversational STM/MTM/LTM)
- **Semantic Memory** (profile/factual extraction)
- **Neural Graph** (bio-inspired linking with LTP - 31 modules)
- **Wave/Resonance Memory** (physics-inspired retrieval)
- **Knowledge Graph** (entity extraction & reasoning)

The architecture transforms generic chatbots into context-aware, personalized AI assistants.

---

## Table of Contents

1. [System Architecture Diagram](#system-architecture-diagram)
2. [Core Memory Types](#core-memory-types)
3. [Neural Graph Module (31 files)](#neural-graph-module)
4. [Wave Memory Module](#wave-memory-module)
5. [Knowledge Graph Module](#knowledge-graph-module)
6. [Common Infrastructure](#common-infrastructure)
7. [Server & API](#server--api)
8. [Benchmarking](#benchmarking)
9. [Configuration](#configuration)
10. [Data Types Reference](#data-types-reference)

---

## System Architecture Diagram

```
+==============================================================================+
|                           MEMMACHINE ARCHITECTURE                            |
|              Cognitive Memory System for LLM Agents (v2.0)                   |
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
|                         MAIN ORCHESTRATOR (memmachine.py)                    |
|  +------------------------------------------------------------------------+  |
|  |  create_session | add_episodes | query_search | list_search | delete   |  |
|  +------------------------------------------------------------------------+  |
+------------------------------------------------------------------------------+
          |                    |                    |                    |
          v                    v                    v                    v
+==================+  +==================+  +==================+  +==================+
|  EPISODIC        |  |  SEMANTIC        |  |  NEURAL GRAPH    |  |  KNOWLEDGE       |
|  MEMORY          |  |  MEMORY          |  |  (31 modules)    |  |  GRAPH           |
+==================+  +==================+  +==================+  +==================+
| • STM (session)  |  | • Profile facts  |  | • 4-Layer Hier.  |  | • Entity extract |
| • MTM (hours)    |  | • User prefs     |  | • LTP dynamics   |  | • Relation map   |
| • LTM (persist)  |  | • Background     |  | • Multi-retrieval|  | • Think-on-Graph |
|                  |  |   ingestion      |  | • Consolidation  |  |                  |
+==================+  +==================+  +==================+  +==================+

+==============================================================================+
|                     NEURAL GRAPH ARCHITECTURE (Bio-Inspired)                 |
+==============================================================================+

  +-------------------------------------------------------------------------+
  |                         4-LAYER HIERARCHY                                |
  |                                                                          |
  |  Layer 3: PERSONA    ────────────────────────  (Stable User Model)      |
  |       │                                                                  |
  |  Layer 2: TOPIC      ──────────────────  (Semantic Clusters)            |
  |       │                                                                  |
  |  Layer 1: EPISODE    ────────────  (Session Groupings)                  |
  |       │                                                                  |
  |  Layer 0: MESSAGE    ────  (Raw Conversation Messages)                  |
  |                                                                          |
  |  Layer 4: TEMPORAL   ────  (Date/Time Anchor Nodes)                     |
  +-------------------------------------------------------------------------+

  +-------------------------------------------------------------------------+
  |                           EDGE TYPES                                     |
  |                                                                          |
  |  • TEMPORAL      → Recency-biased propagation (hippocampus)             |
  |  • SEMANTIC      → Hard similarity threshold (neocortex)                |
  |  • ENTITY        → Co-occurrence relations                               |
  |  • CAUSAL        → Explicit causality chains                             |
  |  • HIERARCHY     → Layer transitions                                     |
  |  • CO_ACTIVATION → Hebbian learning (LTP - "fire together, wire together")|
  +-------------------------------------------------------------------------+

  +-------------------------------------------------------------------------+
  |                      RETRIEVAL ARCHITECTURES                             |
  |                                                                          |
  |  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     |
  |  │  Standard   │  │   Flash     │  │  Electron   │  │  Tesseract  │     |
  |  │  Retriever  │  │  Retriever  │  │  Retriever  │  │    4D       │     |
  |  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘     |
  |  Multi-stage     Parallel         Electrical        Brain-inspired      |
  |  BFS/DFS         resonance        charge prop.      store special.      |
  +-------------------------------------------------------------------------+

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
|                           COMMON COMPONENTS                                   |
+==============================================================================+

+------------------------+  +------------------------+  +------------------------+
|      EMBEDDERS         |  |   LANGUAGE MODELS      |  |      RERANKERS         |
+------------------------+  +------------------------+  +------------------------+
| - OpenAI Embedder      |  | - OpenAI Chat          |  | - BM25 Reranker        |
| - Amazon Bedrock       |  | - OpenAI Responses     |  | - Cross-Encoder        |
| - Sentence Transformer |  | - Amazon Bedrock       |  | - RRF Hybrid           |
| - Ollama (nomic-embed) |  | - Ollama (qwen2.5)     |  | - Date-Aware           |
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
```

---

## Core Memory Types

### Episodic Memory (Conversational)

**Location:** `src/memmachine/episodic_memory/`

Three-tier memory hierarchy for conversation management:

| Tier | Purpose | Storage | Access Time |
|------|---------|---------|-------------|
| **STM** | Session context | In-memory cache | ~1ms |
| **MTM** | Temporal bridge | Configurable | ~10ms |
| **LTM** | Persistent storage | Vector DB + Neo4j | ~50ms |

**Key Files:**
- `episodic_memory.py` - Main orchestrator
- `short_term_memory/` - Session-scoped working memory
- `mid_term_memory/` - Temporal window bridging
- `long_term_memory/` - Vector search + persistence
- `episode_store/` - Storage backends (SQL, Neo4j)

**Features:**
- LLM-based episode summarization
- Privacy sanitization (PII detection)
- Emotional encoding
- Compliance auditing

### Semantic Memory (Profile/Factual)

**Location:** `src/memmachine/semantic_memory/`

Extracts and stores long-term user profile facts.

**Key Files:**
- `semantic_memory.py` - Main orchestrator
- `semantic_ingestion.py` - Feature extraction
- `semantic_llm.py` - LLM-based extraction
- `storage/` - PostgreSQL/Neo4j backends

**Features:**
- Background ingestion task
- Isolation types: USER, ROLE, SESSION
- Vector search with distance filtering
- Citation tracking

---

## Neural Graph Module

**Location:** `src/memmachine/neural_graph/` (31 files)

Bio-inspired memory linking system implementing principles from neuroscience.

### Core Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         NEURAL GRAPH SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│  │   STORAGE    │    │   GATING     │    │  HIERARCHY   │               │
│  │  (nodes/     │    │ (activation  │    │  (4 layers)  │               │
│  │   edges)     │    │  thresholds) │    │              │               │
│  └──────────────┘    └──────────────┘    └──────────────┘               │
│         │                   │                   │                        │
│         └───────────────────┼───────────────────┘                        │
│                             │                                            │
│                             v                                            │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      NEURAL GRAPH SERVICE                         │   │
│  │  • Auto-consolidation  • Temporal linking  • Episode segmentation │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                             │                                            │
│         ┌───────────────────┼───────────────────┐                        │
│         │                   │                   │                        │
│         v                   v                   v                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│  │  RETRIEVER   │    │    FLASH     │    │  ELECTRON    │               │
│  │  (standard)  │    │  (parallel)  │    │  (charge)    │               │
│  └──────────────┘    └──────────────┘    └──────────────┘               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Module Reference

| Category | File | Purpose |
|----------|------|---------|
| **Core Types** | `data_types.py` | NeuralNode, NeuralEdge, NodeLayer, EdgeType |
| **Storage** | `storage.py` | Abstract & in-memory node/edge storage |
| **Gating** | `gating.py` | Non-linear edge activation (strict/permissive/balanced) |
| **Hierarchy** | `hierarchy.py` | 4-layer node management (MESSAGE→EPISODE→TOPIC→PERSONA) |
| **Temporal** | `temporal.py` | LTP dynamics, temporal chains, decay |
| **Consolidation** | `consolidation.py` | Memory pruning, merging, summary hubs |
| **Retrieval** | `retriever.py` | Multi-stage: seed→traverse→accumulate→fire |
| **Flash** | `flash_retriever.py` | Parallel multi-dimensional resonance |
| **Electron** | `electron.py` | Electrical charge propagation model |
| **Query Router** | `query_router.py` | Query type detection (temporal/entity/multi-hop/adversarial) |
| **Tesseract** | `tesseract.py` | 4D brain-inspired store specialization |
| **Holographic** | `holographic_memory.py` | HRR binding for associative memory |
| **Tensor** | `tensor_memory.py` | Multidimensional memory encoding |
| **Wave** | `wave_propagation.py` | Signal wave physics |
| **Resonance** | `resonance_field.py` | Frequency-based memory activation |
| **Complex** | `complex_resonance.py` | Multi-dimensional resonance |
| **Temporal Resolve** | `temporal_resolver.py` | Relative→absolute time conversion |
| **Dialogue** | `dialogue_linker.py` | Cross-message binding |
| **Decomposer** | `query_decomposer.py` | Multi-hop query breakdown |
| **Pattern** | `pattern_completion.py` | Associative pattern recall |
| **LSH** | `lsh.py` | Locality-sensitive hashing |
| **Interference** | `interference.py` | Memory competition effects |
| **Debug** | `debugger.py` | Graph visualization, tracing |
| **Prompts** | `prompts.py` | Retrieval prompt templates |
| **Service** | `service.py` | Main orchestrator |
| **Integration** | `sun_graph_integration.py` | Episodic memory bridge |
| **Context** | `binding_context.py` | Variable binding management |
| **Conversation** | `conversation_sun.py` | Story Understanding Network |
| **Field** | `field_retriever.py` | Field-based retrieval |

### Data Types

```python
class NodeLayer(IntEnum):
    MESSAGE = 0   # Raw conversation messages
    EPISODE = 1   # Session groupings
    TOPIC = 2     # Semantic clusters
    PERSONA = 3   # Stable user model
    TEMPORAL = 4  # Date/time anchors

class EdgeType(Enum):
    TEMPORAL       # Recency-biased propagation
    SEMANTIC       # Similarity threshold
    ENTITY         # Co-occurrence
    CAUSAL         # Causality chains
    HIERARCHY      # Layer transitions
    CO_ACTIVATION  # Hebbian learning (LTP)

@dataclass
class NeuralNode:
    node_id: str
    layer: NodeLayer
    content: str
    embedding: List[float]
    activation: float  # 0.0-1.0
    consolidation_state: ConsolidationState
    wave_amplitudes: Dict[str, float]
    created_at: datetime
    last_activated: datetime

@dataclass
class NeuralEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_type: EdgeType
    base_weight: float  # 0.0-1.0
    sign: int  # +1 excitatory, -1 inhibitory
    ltp_boost: float  # Long-term potentiation
    propagation_delay_ms: float
    activation_count: int
    ltp_decay_rate: float
```

### Long-Term Potentiation (LTP)

Biological memory strengthening:

```python
# On repeated co-activation ("fire together, wire together")
edge.ltp_boost *= (1 + learning_rate)
edge.activation_count += 1

# Temporal decay (biological forgetting)
days_since = (now - edge.last_activated).days
edge.ltp_boost *= (decay_rate ** days_since)
```

### Tesseract 4D Memory

Brain-inspired store specialization:

```
Query Type Detection → Specialized Store Selection → Weighted Fusion

┌─────────────────────────────────────────────────────────────┐
│  Temporal Store (Hippocampus)    → "when" questions        │
│  Entity Store (Neocortex)        → "what/who" questions    │
│  Reasoning Store (Prefrontal)    → multi-hop chains        │
│  Adversarial Store (Orbitofrontal) → conflict detection    │
└─────────────────────────────────────────────────────────────┘
```

---

## Wave Memory Module

**Location:** `src/memmachine/wave_memory/`

Physics-inspired resonance retrieval.

**Key Concept:** Every query is a wave. Find memories whose waves resonate.

**Signal Dimensions:**
- Temporal (when)
- Entity (who/what)
- Relation (how connected)
- Action (what happened)
- State (current status)

**Files:**
- `wave_resonance.py` - Resonance retrieval without classification
- `wave_encoder.py` - Memory→wave encoding
- `theta_gamma_coupling.py` - Neural rhythm oscillations
- `temporal_calculator.py` - Temporal signal extraction
- `signal_extractors/` - Multi-dimensional signal extraction

---

## Knowledge Graph Module

**Location:** `src/memmachine/knowledge_graph/`

Entity extraction and structured reasoning.

**Components:**
- `knowledge_graph_service.py` - Orchestration
- `entity_extractor.py` - Hybrid extraction (LLM + NER)
- `entity_verifier.py` - Verification
- `graph_reasoner.py` - Think-on-Graph with MeKB scoring

**Flow:**
```
Text → Entity Extractor → Entity Verifier → Graph Store → Graph Reasoner
```

---

## Common Infrastructure

**Location:** `src/memmachine/common/`

### Configuration (`configuration/`)

```yaml
episodic_memory:
  enabled: true
  short_term_memory:
    llm_model: gpt-4
  long_term_memory:
    embedder: openai
    reranker: cross_encoder

semantic_memory:
  database: postgres_db
  llm_model: gpt-4

resources:
  embedders:
    openai:
      model: text-embedding-3-large
  language_models:
    gpt-4:
      api_key: "${OPENAI_API_KEY}"
  rerankers:
    cross_encoder:
      model_name: ms-marco-MiniLM-L-12-v2
  databases:
    postgres_db:
      url: "postgresql://..."
```

### Resource Managers (`resource_manager/`)

| Manager | Purpose |
|---------|---------|
| `database_manager.py` | Connection pooling |
| `embedder_manager.py` | Embedder instances |
| `language_model_manager.py` | LLM instances |
| `reranker_manager.py` | Reranker creation |

### Embedders (`embedder/`)

| Embedder | File | Models |
|----------|------|--------|
| OpenAI | `openai_embedder.py` | text-embedding-3-* |
| Bedrock | `amazon_bedrock_embedder.py` | Titan, Claude |
| HuggingFace | `sentence_transformer_embedder.py` | all-MiniLM-*, etc. |

### Rerankers (`reranker/`)

| Reranker | File | Method |
|----------|------|--------|
| BM25 | `bm25_reranker.py` | Lexical matching |
| Cross-Encoder | `cross_encoder_reranker.py` | Neural reranking |
| RRF Hybrid | `rrf_hybrid_reranker.py` | Reciprocal Rank Fusion |
| Date-Aware | `date_aware_reranker.py` | Temporal recency boost |

### Privacy (`privacy_sanitizer/`)

- PII detection (email, phone, SSN, credit cards)
- Data masking/redaction
- GDPR compliance
- Unlearning/deletion support

### Emotional Encoding (`emotional_encoder/`)

- Emotion detection (LLM + rule-based)
- Mood state tracking
- Emotional resonance scoring

### Temporal Normalization (`temporal_normalizer/`)

```python
"yesterday" → 2025-12-10
"next week" → 2025-12-18
"last March" → 2025-03-XX
```

---

## Server & API

**Location:** `src/memmachine/server/`

### FastAPI Application (`app.py`)

```bash
python -m memmachine.server.app --host 0.0.0.0 --port 8080
```

### API Endpoints (`api_v2/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sessions` | POST | Create session |
| `/sessions/{id}` | GET | Get session |
| `/episodes` | POST | Add episodes |
| `/search` | POST | Query search |
| `/list` | GET | List memories |

### MCP Support

- `mcp_http.py` - HTTP transport
- `mcp_stdio.py` - Stdio transport

### REST Client (`rest_client/`)

```python
from memmachine.rest_client import MemMachineClient

client = MemMachineClient(api_key="...", base_url="http://localhost:8080")
memory = client.memory(user_id="...", session_id="...")
memory.add("I like pizza")
results = memory.search("What food do I like?")
```

---

## Benchmarking

**Location:** `evaluation/`

### LoCoMo Benchmark Categories

| Category | Description | Target Accuracy |
|----------|-------------|-----------------|
| Single-hop | Direct fact lookup | 85%+ |
| Temporal | "When did X happen?" | 80%+ |
| Multi-hop | Multiple inference steps | 70%+ |
| Open-domain | Free-form questions | 65%+ |
| Adversarial | Negation, conflicts | 75%+ |

### Benchmark Implementations

| Benchmark | File | Architecture |
|-----------|------|--------------|
| Tesseract | `locomo10_tesseract_benchmark.py` | 4D brain-inspired stores |
| Hybrid | `locomo10_hybrid_benchmark.py` | Mixed routing |
| Electron | `locomo10_electron_benchmark.py` | Electrical activation |
| Resonance | `locomo10_resonance_benchmark.py` | Wave resonance |
| 4D Holographic | `locomo10_4d_benchmark.py` | Holographic memory |
| Graph-First | `locomo10_graph_first_benchmark.py` | Graph traversal |
| Brain-Inspired | `locomo10_brain_inspired_benchmark.py` | Bio-inspired |
| Galactic | `locomo10_galactic_benchmark.py` | Large-scale |
| Tensor | `locomo10_tensor_benchmark.py` | Tensor encoding |
| Field | Integrated | Field-based retrieval |

### Running Benchmarks

```bash
python evaluation/locomo10_tesseract_benchmark.py
```

Results saved to `evaluation/results/*.json`

---

## Configuration

### Full Configuration Example

```yaml
episodic_memory:
  enabled: true
  privacy_enabled: true
  emotional_encoding_enabled: true
  mid_term_memory_enabled: true
  knowledge_graph_enabled: true
  granularity_routing_enabled: true
  domain_classification_enabled: true
  short_term_memory:
    llm_model: gpt-4
    summary_prompt_system: "Summarize the conversation..."
  long_term_memory:
    embedder: openai
    reranker: cross_encoder
    vector_graph_store: default_store

semantic_memory:
  database: postgres_db
  llm_model: gpt-4
  embedding_model: openai

resources:
  embedders:
    openai:
      model: text-embedding-3-large
      api_key: "${OPENAI_API_KEY}"
  language_models:
    gpt-4:
      api_key: "${OPENAI_API_KEY}"
  rerankers:
    cross_encoder:
      model_name: ms-marco-MiniLM-L-12-v2
  databases:
    postgres_db:
      url: "postgresql://user:pass@localhost/memmachine"
    neo4j_db:
      url: "bolt://localhost:7687"

neural_graph:
  enabled: true
  consolidation_interval: 3600  # seconds
  ltp_learning_rate: 0.1
  ltp_decay_rate: 0.95

server:
  host: localhost
  port: 8080

logging:
  level: INFO

benchmarks:
  ollama_model: "qwen2.5:7b-instruct"
  judge_model: "qwen2.5:7b-instruct"
  embedding_model: "nomic-embed-text"
  top_k_retrieval: 30
  context_limit: 15000
```

---

## Data Types Reference

### Episode Types

```python
@dataclass
class Episode:
    uid: str
    session_key: str
    created_at: datetime
    content: str
    metadata: Dict[str, Any]
    sensitivity: SensitivityLevel

@dataclass
class EpisodeEntry:
    role: str  # "user" or "assistant"
    content: str
    type: EpisodeType
```

### Semantic Feature

```python
@dataclass
class SemanticFeature:
    feature_id: str
    set_id: str
    feature_data: str
    embedding: List[float]
    source_episode_ids: List[str]
    created_at: datetime
```

### Search Response

```python
@dataclass
class SearchResponse:
    episodic_results: List[Episode]
    semantic_results: List[SemanticFeature]
    neural_graph_results: List[NeuralNode]
    total_count: int
```

---

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
├── neural_graph/                     # 31 files - bio-inspired memory
│   ├── data_types.py                 # Core types
│   ├── storage.py                    # Node/edge storage
│   ├── gating.py                     # Activation gates
│   ├── hierarchy.py                  # 4-layer management
│   ├── temporal.py                   # LTP dynamics
│   ├── consolidation.py              # Memory compression
│   ├── retriever.py                  # Multi-stage retrieval
│   ├── flash_retriever.py            # Parallel resonance
│   ├── electron.py                   # Electrical model
│   ├── tesseract.py                  # 4D brain stores
│   ├── holographic_memory.py         # HRR binding
│   └── ...                           # 19 more modules
├── knowledge_graph/                  # Entity extraction
│   ├── entity_extractor.py
│   ├── entity_verifier.py
│   └── graph_reasoner.py
├── wave_memory/                      # Wave resonance
│   ├── wave_resonance.py
│   ├── wave_encoder.py
│   └── theta_gamma_coupling.py
├── sharded_memory/                   # Domain sharding
│   ├── memory_store.py
│   ├── shard_router.py
│   └── domain_hierarchy.py
└── common/
    ├── configuration/                # Config management
    ├── resource_manager/             # DI container
    ├── embedder/                     # Vector embeddings
    ├── language_model/               # LLM interfaces
    ├── reranker/                     # Hybrid reranking
    ├── privacy_sanitizer/            # PII handling
    ├── emotional_encoder/            # Emotion analysis
    ├── domain_classifier/            # Memory sharding
    ├── granularity_router/           # MemGAS
    ├── temporal_normalizer/          # Date normalization
    ├── episode_store/                # Persistence layer
    ├── vector_graph_store/           # Neo4j integration
    └── metrics_factory/              # Prometheus metrics
```

---

## Key Performance Characteristics

| Component | Latency | Purpose |
|-----------|---------|---------|
| STM Lookup | ~1ms | Recent context |
| MTM Search | ~10ms | Temporal segments |
| LTM Vector Search | ~50ms | Semantic similarity |
| BM25 Rerank | ~5ms | Keyword boosting |
| Neural Graph Traversal | ~20ms | Bio-inspired linking |
| KG Reasoning | ~100ms | Entity relationships |
| LLM Generation | ~2-5s | Answer synthesis |

---

## Integration Flow

### Ingestion Flow

```
Input Episode
     │
     v
+--------------------+
| Privacy Sanitizer  |────> [BLOCKED if critical PII]
+--------------------+
     │
     v
+--------------------+
| Emotional Encoder  |────> [emotions, mood state]
+--------------------+
     │
     v
+--------------------+
| Domain Classifier  |────> [domain shard assignment]
+--------------------+
     │
     +──────────+──────────+──────────+
     │          │          │          │
     v          v          v          v
+--------+ +--------+ +--------+ +---------------+
|  STM   | |  MTM   | |  LTM   | | Neural Graph  |
+--------+ +--------+ +--------+ +---------------+
                                       │
                                       v
                                +-------------+
                                |     KG      |
                                | (Entities)  |
                                +-------------+
```

### Retrieval Flow

```
Query
  │
  v
+----------------------+
| Query Router         |────> [type: temporal/entity/multi-hop/adversarial]
+----------------------+
  │
  v
+----------------------+
| Granularity Router   |────> [entropy analysis, level selection]
+----------------------+
  │
  v
+----------------------+
| Domain Classifier    |────> [shard routing]
+----------------------+
  │
  +──────────+──────────+──────────+──────────+
  │          │          │          │          │
  v          v          v          v          v
+------+ +------+ +------+ +----------+ +------+
| STM  | | MTM  | | LTM  | | Neural   | |  KG  |
|search| |search| |search| |  Graph   | |reason|
+------+ +------+ +------+ +----------+ +------+
  │          │          │          │          │
  +──────────+──────────+──────────+──────────+
  │
  v
+----------------------+
| Reranker (Hybrid)    |────> [BM25 + Semantic + Date-Aware]
+----------------------+
  │
  v
[Ranked Results with Context]
```

---

*Generated: 2025-12-11*
*MemMachine Architecture Documentation v2.0*

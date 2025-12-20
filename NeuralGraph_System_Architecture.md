# NeuralGraph System Architecture Document

**Version:** 1.0
**Date:** December 19, 2025
**Project:** MemMachine - Neural Memory System

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Overview & Design Philosophy](#2-system-overview--design-philosophy)
3. [Core Components & Modules](#3-core-components--modules)
4. [Data Flow Architecture](#4-data-flow-architecture)
5. [Advanced Algorithms & Mechanisms](#5-advanced-algorithms--mechanisms)
6. [Configuration & Deployment](#6-configuration--deployment)

---

# 1. Executive Summary

## 1.1 Purpose

NeuralGraph is a neural-inspired memory linking system designed as the core intelligence layer for MemMachine. It implements a biologically-plausible graph-based memory architecture that models how the human brain stores, retrieves, and consolidates memories.

## 1.2 Key Capabilities

| Capability | Description |
|------------|-------------|
| **4-Level Hierarchy** | MESSAGE → EPISODE → TOPIC → PERSONA memory layers |
| **Multi-Stage Retrieval** | 5-stage pipeline with vector search, graph traversal, and pattern completion |
| **Biological Dynamics** | LTP, STDP, Hebbian learning, refractory periods |
| **Intelligent Routing** | Query-aware strategy selection (entity, temporal, multi-hop) |
| **Memory Consolidation** | Automatic pruning, merging, promotion, and eviction |
| **Temporal Reasoning** | Causality chains, temporal anchors, before/after relationships |

## 1.3 Design Philosophy

> *"Signals propagate through a structured computation graph, are gated non-linearly, consolidated by pruning/compression, and scheduled over time."*

The system draws from three domains:
- **Neuroscience**: Hebbian learning, spike-timing plasticity, neural gating
- **Information Retrieval**: Hybrid vector-keyword search, reranking, query routing
- **Graph Theory**: Multi-hop traversal, hub detection, community clustering

---

# 2. System Overview & Design Philosophy

## 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         NeuralGraphService                          │
│                    (High-Level Orchestrator)                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │   Query     │  │  Retrieval  │  │ Consolidation│  │  Temporal  │ │
│  │   Router    │  │   Layer     │  │   Manager    │  │   Manager  │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬─────┘ │
│         │                │                │                │        │
│  ┌──────┴────────────────┴────────────────┴────────────────┴──────┐ │
│  │                     Graph Management Layer                      │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│ │
│  │  │Hierarchy │  │ Gating   │  │ Dialogue │  │  Edge Registry   ││ │
│  │  │ Manager  │  │ System   │  │  Linker  │  │                  ││ │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘│ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                        Storage Layer                            │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │ │
│  │  │  InMemory       │  │    SQLite       │  │    LSH Index    │ │ │
│  │  │  Storage        │  │    Storage      │  │                 │ │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

## 2.2 Module Organization

```
NeuralGraph/
├── Core Data Layer
│   ├── data_types.py          # Node, Edge, Enums
│   ├── storage.py             # Abstract storage interface
│   ├── sqlite_storage.py      # Persistent implementation
│   └── lsh.py                 # Locality-sensitive hashing
│
├── Graph Management Layer
│   ├── hierarchy.py           # 4-layer node hierarchy
│   ├── temporal.py            # Temporal chains & causality
│   ├── consolidation.py       # Memory compression & pruning
│   ├── gating.py              # Non-linear edge activation
│   └── dialogue_linker.py     # Cross-message bindings
│
├── Retrieval Layer
│   ├── retriever.py           # Multi-stage pipeline
│   ├── flash_retriever.py     # Parallel resonance
│   ├── electron_retriever.py  # Electrical simulation
│   ├── query_router.py        # Intelligent query analysis
│   ├── external_retriever.py  # Open-domain sources
│   └── pattern_completion.py  # CA3-inspired recovery
│
├── Advanced Features
│   ├── wavefront.py           # Systolic propagation
│   ├── tesseract.py           # 4D temporal-spatial mapping
│   ├── reranker.py            # Cross-encoder reranking
│   └── attribution.py         # Per-stage result tracking
│
└── Integration
    ├── service.py             # Main service coordinator
    ├── prompts.py             # LLM prompt templates
    └── temporal_utils.py      # Date/time parsing
```

## 2.3 Biological Inspiration

The system models key neuroscience principles:

| Principle | Implementation |
|-----------|----------------|
| **Hebbian Learning** | CO_ACTIVATION edges strengthen when nodes fire together |
| **STDP** | Spike-timing dependent plasticity for temporal edges |
| **LTP** | Long-term potentiation: up to 3× boost from repeated activation |
| **Refractory Period** | Temporary node inactivation post-firing |
| **Neural Gating** | Sigmoid/ReLU activation functions on edges |
| **Temporal Summation** | Signal integration over time windows |

---

# 3. Core Components & Modules

## 3.1 Data Structures

### 3.1.1 NeuralNode

The fundamental memory unit representing information at various abstraction levels.

```
NeuralNode
├── Identity
│   ├── node_id: str (UUID)
│   ├── session_key: str
│   └── layer: NodeLayer (MESSAGE|EPISODE|TOPIC|PERSONA)
│
├── Content
│   ├── content: str (text representation)
│   ├── embedding: ndarray (vector embedding)
│   └── entity_ids: List[str] (referenced entities)
│
├── Neural Properties
│   ├── wave_amplitudes: Dict[str, float] (multi-dimensional signals)
│   ├── heat_score: float (0-10, activation level)
│   ├── importance_score: float (intrinsic relevance)
│   └── refractory_period_ms: int (post-activation cooldown)
│
├── Hierarchy
│   ├── parent_id: Optional[str]
│   └── child_ids: List[str]
│
├── Lifecycle
│   ├── consolidation_state: ConsolidationState
│   ├── created_at: datetime
│   └── last_activated: datetime
│
└── Metadata
    ├── edge_type_counts: Dict[EdgeType, int]
    └── dominant_dimension: str
```

### 3.1.2 NeuralEdge

Weighted, directional connections with biological dynamics.

```
NeuralEdge
├── Identity
│   ├── edge_id: str (canonical ordering)
│   ├── source_id: str
│   └── target_id: str
│
├── Type & Weight
│   ├── edge_type: EdgeType
│   ├── base_weight: float [0,1]
│   ├── sign: int (+1 excitatory, -1 inhibitory)
│   └── confidence: float [0,1]
│
├── Biological Dynamics
│   ├── activation_count: int
│   ├── ltp_boost: float (1.0-3.0)
│   ├── gate_threshold: float
│   └── propagation_delay_ms: int
│
└── Computed Properties
    └── effective_weight = base_weight × confidence × recency_decay × ltp_boost
```

### 3.1.3 Enumerations

**NodeLayer (4-Level Hierarchy)**
```
MESSAGE (0)   → Raw conversation messages
EPISODE (1)   → Grouped message segments
TOPIC (2)     → Semantic clusters
PERSONA (3)   → Stable user models
TEMPORAL (4)  → Date/time anchors
```

**EdgeType (6 Specialized Types)**
```
TEMPORAL      → Sequential causality (before/after)
SEMANTIC      → Embedding similarity links
ENTITY        → Entity co-occurrence
CAUSAL        → Explicit cause-effect
HIERARCHY     → Parent-child transitions
CO_ACTIVATION → Hebbian learning links
```

**QueryType (8 Classifications)**
```
ENTITY_ATTRIBUTE   → "What is X's job?"
ENTITY_ACTION      → "What did X do?"
ENTITY_RELATION    → "Who is X's friend?"
TEMPORAL_WHEN      → "When did X happen?"
TEMPORAL_SEQUENCE  → "What happened after X?"
TEMPORAL_DURATION  → "How long did X last?"
MULTI_HOP          → "What did the person who..."
ADVERSARIAL        → Negations, hypotheticals
```

## 3.2 Storage Layer

### 3.2.1 InMemoryNeuralGraphStorage

Primary implementation with optimized indexing:

| Index | Purpose | Complexity |
|-------|---------|------------|
| `_nodes` | node_id → NeuralNode | O(1) access |
| `_edges` | edge_id → NeuralEdge | O(1) access |
| `_nodes_by_session` | session → node_ids | Session isolation |
| `_nodes_by_entity` | entity → node_ids | Fast entity lookup |
| `_nodes_by_layer` | session → layer → nodes | Hierarchy access |
| `_edges_from/_to` | source/target indices | O(1) traversal |

**LSH (Locality-Sensitive Hashing)**
- Enables O(k·d) vector search vs O(n·d) brute-force
- One LSH index per session for isolation
- Multi-probe for higher recall
- Fallback to brute-force for small sessions (<100 nodes)

### 3.2.2 SQLiteNeuralGraphStorage

Production-ready persistent storage for durability.

## 3.3 Retrieval Layer

### 3.3.1 NeuralRetriever (Multi-Stage Pipeline)

The core retrieval orchestrator implementing 5 stages:

```
┌────────────────────────────────────────────────────────────────┐
│                    RETRIEVAL PIPELINE                          │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Stage 1: FAST RECALL                                          │
│  ├── Vector similarity search (LSH/brute-force)                │
│  ├── Keyword hybrid search                                     │
│  └── Wave-routed pre-population                                │
│                         ↓                                      │
│  Stage 2: ENTITY EXPANSION                                     │
│  ├── Traverse ENTITY edges (3 hops)                            │
│  └── Traverse SEMANTIC edges                                   │
│                         ↓                                      │
│  Stage 3: TEMPORAL CHAIN                                       │
│  ├── Follow TEMPORAL edges (3 hops)                            │
│  └── Follow CAUSAL edges                                       │
│                         ↓                                      │
│  Stage 4: HIERARCHY TRAVERSAL                                  │
│  ├── Move up to parent layers (2 hops)                         │
│  └── Move down to child layers                                 │
│                         ↓                                      │
│  Stage 5: CO-ACTIVATION BOOST                                  │
│  └── Hebbian links (1 hop)                                     │
│                         ↓                                      │
│  FINAL RERANKING                                               │
│  ├── CA3 Pattern Completion                                    │
│  ├── Wavefront Propagation                                     │
│  └── Multi-signal scoring                                      │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**Reranking Signals & Weights:**

| Signal | Weight | Description |
|--------|--------|-------------|
| Base Score | 50% | Initial retrieval score |
| Keyword Match | 70% | Fact anchor matching |
| Entity Overlap | 35% | Entity mention alignment |
| Heat Score | 30% | Node activity level |
| Importance | 25% | Intrinsic relevance |
| Wave Alignment | 18% | Signal dimension match |

### 3.3.2 QueryRouter

Intelligent query analysis for optimal retrieval strategy:

```
Query: "What did Caroline say about her job?"
         ↓
    QueryRouter.analyze()
         ↓
QueryAnalysis:
├── type: ENTITY_ACTION
├── entities: ["Caroline"]
├── primary_subject: "Caroline"
├── speaker_filter: "Caroline"
├── confidence: 0.92
└── strategy: "entity_filter"
```

**Pattern Recognition:**
- 40+ entity-focused patterns
- 50+ temporal patterns
- Multi-hop detection
- Adversarial query handling
- Open-domain detection

### 3.3.3 Retriever Variants

| Retriever | Mechanism | Use Case |
|-----------|-----------|----------|
| **NeuralRetriever** | Multi-stage pipeline | Default, comprehensive |
| **FlashRetriever** | Parallel resonance | Fast parallel activation |
| **ElectronRetriever** | Electrical simulation | Bio-realistic modeling |
| **ExternalRetriever** | Open-domain sources | World knowledge fallback |

## 3.4 Graph Management

### 3.4.1 HierarchyManager

Manages the 4-level memory hierarchy:

```
                    ┌─────────────┐
                    │   PERSONA   │  Layer 3: Stable user model
                    │  (User X)   │
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────┴──────┐ ┌──────┴──────┐ ┌──────┴──────┐
    │    TOPIC    │ │    TOPIC    │ │    TOPIC    │  Layer 2
    │   (Work)    │ │  (Family)   │ │  (Hobbies)  │
    └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
           │               │               │
    ┌──────┴──────┐ ┌──────┴──────┐ ┌──────┴──────┐
    │   EPISODE   │ │   EPISODE   │ │   EPISODE   │  Layer 1
    │ (Session 1) │ │ (Session 2) │ │ (Session 3) │
    └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
           │               │               │
    ┌──┬──┬┴┬──┐    ┌──┬──┬┴┬──┐    ┌──┬──┬┴┬──┐
    │M1│M2│M3│M4│   │M5│M6│M7│M8│   │M9│...    │  Layer 0
    └──┴──┴──┴──┘   └──┴──┴──┴──┘   └──┴──┴──┴──┘
         MESSAGE nodes (raw conversation)
```

### 3.4.2 TemporalChainManager

Manages temporal relationships and biological dynamics:

| Feature | Description |
|---------|-------------|
| Temporal Edges | BEFORE/AFTER with propagation delays |
| STDP | Strengthen edges based on firing sequence |
| LTP Decay | Reduce old connection strengths |
| Causal Chains | Explicit cause-effect detection |
| Temporal Summation | Signal integration over windows |

### 3.4.3 ConsolidationManager

Memory lifecycle management:

```
┌─────────────────────────────────────────────────────────────┐
│                  CONSOLIDATION CYCLE                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. DECAY HEAT                                              │
│     └── Apply layer-specific decay rates (Ebbinghaus)       │
│                                                             │
│  2. PRUNE EDGES                                             │
│     └── Remove edges where effective_weight < threshold     │
│                                                             │
│  3. MERGE SIMILAR                                           │
│     └── Combine nodes with cosine_sim > 0.72                │
│                                                             │
│  4. CREATE HUBS                                             │
│     └── Summarize dense clusters (compression)              │
│                                                             │
│  5. PROMOTE NODES                                           │
│     └── Move hot nodes (heat > 2.5) up hierarchy            │
│                                                             │
│  6. EVICT NODES                                             │
│     └── Remove cold nodes (heat < 0.25)                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Heat Thresholds:**
- Initial heat: 0.5
- Promotion threshold: 2.5 (reachable in ~6-7 activations)
- Eviction threshold: 0.25
- Decay rate: 1.5% daily (Ebbinghaus curve)

### 3.4.4 EdgeGateRegistry

Non-linear activation functions per edge type:

```python
# Gating mechanism
def gate(input_signal, edge):
    threshold = edge.gate_threshold
    return sigmoid(steepness * (input_signal - threshold))
```

| Edge Type | Gate Behavior |
|-----------|---------------|
| TEMPORAL | Recency-biased (recent = higher gate) |
| SEMANTIC | Hard similarity threshold (>0.3) |
| ENTITY | Co-occurrence + explicit relations |
| CAUSAL | Strict explicit causality |
| HIERARCHY | Layer transition probability |
| CO_ACTIVATION | Hebbian link strength |

---

# 4. Data Flow Architecture

## 4.1 Ingestion Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                      INGESTION FLOW                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   EPISODE INPUT                                                 │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │            process_episodes()                           │   │
│   │   ├── Extract text content                              │   │
│   │   ├── Generate embedding                                │   │
│   │   └── Compute wave_amplitudes                           │   │
│   └─────────────────────────────────────────────────────────┘   │
│        │                                                        │
│        ▼                                                        │
│   MESSAGE NODE (Layer 0)                                        │
│        │                                                        │
│        ├──────────────────────────────────────────────┐         │
│        │                                              │         │
│        ▼                                              ▼         │
│   ┌────────────────────┐                    ┌─────────────────┐ │
│   │ build_temporal_    │                    │ create_entity_  │ │
│   │ chain()            │                    │ edges()         │ │
│   │ └── TEMPORAL edges │                    │ └── ENTITY edges│ │
│   └────────────────────┘                    └─────────────────┘ │
│        │                                              │         │
│        └──────────────────┬───────────────────────────┘         │
│                           ▼                                     │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │         create_semantic_edges_batch()                   │   │
│   │         └── SEMANTIC edges (similarity > threshold)     │   │
│   └─────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │         create_dialogue_links()                         │   │
│   │         └── Q/A pairs, topic threads, coreference       │   │
│   └─────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │         Post-Ingestion Consolidation Check              │   │
│   │         └── Identify promotion candidates               │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 4.2 Retrieval Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                      RETRIEVAL FLOW                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   QUERY INPUT: "What did Caroline say about her job?"           │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │   QueryRouter.analyze()                                 │   │
│   │   ├── type: ENTITY_ACTION                               │   │
│   │   ├── entities: ["Caroline"]                            │   │
│   │   └── strategy: "entity_filter"                         │   │
│   └─────────────────────────────────────────────────────────┘   │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │   FilteredRetriever.get_filtered_candidates()           │   │
│   │   └── Boost mode: matching entities get 1.5-1.8× boost  │   │
│   └─────────────────────────────────────────────────────────┘   │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │   Stage 1: FAST RECALL                                  │   │
│   │   ├── Vector search (LSH)                               │   │
│   │   ├── Keyword hybrid                                    │   │
│   │   └── Wave-routed pre-population                        │   │
│   │                                                         │   │
│   │   Stage 2: ENTITY EXPANSION (3 hops)                    │   │
│   │   └── Traverse ENTITY/SEMANTIC edges                    │   │
│   │                                                         │   │
│   │   Stage 3: TEMPORAL CHAIN (3 hops)                      │   │
│   │   └── Follow TEMPORAL/CAUSAL edges                      │   │
│   │                                                         │   │
│   │   Stage 4: HIERARCHY TRAVERSAL (2 hops)                 │   │
│   │   └── Navigate parent/child layers                      │   │
│   │                                                         │   │
│   │   Stage 5: CO-ACTIVATION (1 hop)                        │   │
│   │   └── Hebbian links                                     │   │
│   └─────────────────────────────────────────────────────────┘   │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │   ADVANCED PROCESSING                                   │   │
│   │   ├── CA3 Pattern Completion (Hopfield attractor)       │   │
│   │   ├── Wavefront Propagation (interference detection)    │   │
│   │   └── Multi-signal Reranking                            │   │
│   └─────────────────────────────────────────────────────────┘   │
│        │                                                        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │   POST-PROCESSING                                       │   │
│   │   ├── Dialogue Link Expansion                           │   │
│   │   ├── STDP Application                                  │   │
│   │   └── Co-activation Recording                           │   │
│   └─────────────────────────────────────────────────────────┘   │
│        │                                                        │
│        ▼                                                        │
│   RETRIEVAL RESULT (ranked nodes with metadata)                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 4.3 Session Isolation

Each session maintains complete data isolation:

```
┌─────────────────────────────────────────────────────────────────┐
│                     SESSION ISOLATION                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Session A                    Session B                        │
│   ┌──────────────────────┐    ┌──────────────────────┐          │
│   │  Nodes: A1, A2, A3   │    │  Nodes: B1, B2, B3   │          │
│   │  Edges: A1→A2, etc.  │    │  Edges: B1→B2, etc.  │          │
│   │  LSH Index: idx_A    │    │  LSH Index: idx_B    │          │
│   │  Entity Index        │    │  Entity Index        │          │
│   └──────────────────────┘    └──────────────────────┘          │
│            ↓                            ↓                       │
│   ┌────────────────────────────────────────────────────────┐    │
│   │              Shared Storage Layer                      │    │
│   │  (Indexes partitioned by session_key)                  │    │
│   └────────────────────────────────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

# 5. Advanced Algorithms & Mechanisms

## 5.1 CA3 Pattern Completion

Inspired by the hippocampal CA3 region, implements Hopfield-style attractor dynamics:

```
Algorithm: CA3 Pattern Completion
─────────────────────────────────
1. SEED with top-K candidates from retrieval
2. REPEAT for max_iterations:
   a. For each candidate node:
      - Compute activation from connected nodes
      - Apply non-linear threshold
   b. SPREAD activation to neighbors
   c. CHECK convergence (stable state)
3. RETURN nodes with activation > threshold
```

**Properties:**
- Recovers complete patterns from partial cues
- Handles noisy/incomplete queries
- Converges to stable attractor states

## 5.2 Wavefront Propagation

Systolic-style diagonal propagation through TIME × HIERARCHY:

```
        TIME →
      ─────────────────────────
    H │   ·   ·   *   ·   ·   │
    I │   ·   *   ·   ·   ·   │ * = Wavefront
    E │   *   ·   ·   ·   ·   │ · = Nodes
    R │   ·   ·   ·   ·   ·   │
    A │   ·   ·   ·   ·   ·   │
    R ─────────────────────────
    C
    H
    Y
    ↓
```

**Interference Detection:**
- **Constructive**: Nodes aligned with wavefront → 1.2× boost
- **Destructive**: Misaligned nodes → 0.8× penalty
- **Partial**: Slight boost

## 5.3 Long-Term Potentiation (LTP)

```
LTP Dynamics
────────────
activation_count++
ltp_boost = min(3.0, 1.0 + 0.15 × activation_count)
effective_weight = base_weight × ltp_boost × recency_decay
```

**Parameters:**
- Maximum boost: 3.0× (from repeated activation)
- Boost per activation: +0.15
- Decay rate: 0.5% daily

## 5.4 Spike-Timing Dependent Plasticity (STDP)

```
if pre_node fires BEFORE post_node:
    edge.weight *= 1.1  # Strengthen (causal)
elif pre_node fires AFTER post_node:
    edge.weight *= 0.9  # Weaken (anti-causal)
```

## 5.5 Soft Filtering with Boost Mode

Query-aware candidate filtering that avoids information loss:

| Match Type | Soft Mode | Boost Mode |
|------------|-----------|------------|
| Exact speaker match | 1.0× | 1.8× |
| Alias match | 0.9× | 1.5× |
| Entity ID match | 0.85× | 1.4× |
| Content mention | 0.7× | 1.25× |
| Fuzzy mention | 0.5× | 1.15× |
| No match | 0.2× | 1.0× |

## 5.6 Deduplication

Near-duplicate detection during ingestion:

```python
if cosine_similarity(new_node, existing_node) >= 0.92:
    # Skip insertion, return existing node
    return existing_node
```

**Benefits:**
- Prevents ranking dilution
- Reduces storage overhead
- Maintains retrieval precision

---

# 6. Configuration & Deployment

## 6.1 Configuration Profiles

### Single-Hop Optimization (Factual Questions)

```python
config = NeuralGraphConfig(
    query_routing_mode = "boost",        # 1.5-1.8× boost for matches
    reranker_enabled = True,             # Cross-encoder reranking
    gate_profile = "single_hop",         # Optimized gating
    node_dedup_enabled = True,           # Remove near-duplicates
    recency_retention_enabled = True,    # Protect factual nodes
    hop_decay_factor = 0.88,             # Preserve multi-hop signal
)
```

### Multi-Hop Optimization (Reasoning Chains)

```python
config = NeuralGraphConfig(
    query_routing_mode = "soft",         # Preserve candidates
    gate_profile = "multi_hop",          # More permissive gating
    reranker_enabled = True,             # Light reranking
    reranker_top_k = 30,                 # More candidates
)
```

## 6.2 Key Parameters

### Retrieval Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `hop_decay_factor` | 0.88 | Signal decay per hop |
| `max_candidates_per_stage` | 50 | Candidate limit per stage |
| `pattern_completion_iterations` | 4 | CA3 iterations |
| `lsh_num_tables` | 8 | LSH hash tables |
| `lsh_num_bits` | 12 | LSH bits per hash |

### Consolidation Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `promotion_heat_threshold` | 2.5 | Heat for promotion |
| `eviction_heat_threshold` | 0.25 | Heat for eviction |
| `heat_decay_rate` | 0.015 | Daily decay (1.5%) |
| `merge_similarity_threshold` | 0.72 | Merge threshold |
| `prune_weight_threshold` | 0.1 | Edge prune threshold |

### Biological Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_ltp_boost` | 3.0 | Maximum LTP multiplier |
| `ltp_boost_per_activation` | 0.15 | LTP increment |
| `refractory_period_ms` | 100 | Post-fire cooldown |
| `propagation_delay_range` | [10, 50] | Edge delay (ms) |

## 6.3 Performance Characteristics

### Complexity Analysis

| Operation | Complexity | Notes |
|-----------|------------|-------|
| Vector search (LSH) | O(k·d) | k candidates, d dimensions |
| Vector search (brute) | O(n·d) | n nodes, d dimensions |
| Entity lookup | O(1) | Index-based |
| Graph traversal | O(e) | e edges per node |
| Consolidation | O(n + e) | Full graph scan |

### Scalability

- **LSH activation**: Automatic for sessions >100 nodes
- **Batch operations**: Single lock acquisition for N items
- **Session isolation**: Independent scaling per session
- **Concurrent retrieval**: Semaphore limiting (10 max)

## 6.4 Monitoring & Debugging

### Statistics Available

```python
service.get_consolidation_statistics()
# Returns:
# {
#   "total_nodes": 1500,
#   "nodes_by_layer": {...},
#   "edges_pruned": 45,
#   "nodes_merged": 12,
#   "nodes_promoted": 8,
#   "nodes_evicted": 3
# }

storage.get_session_statistics(session_key)
# Returns node/edge counts, deduplication stats
```

### Attribution Tracking

```python
config.attribution_enabled = True
# Traces which retrieval stage each result came from
```

## 6.5 Integration Points

```
┌─────────────────────────────────────────────────────────────────┐
│                    INTEGRATION ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌───────────────┐    ┌───────────────┐    ┌───────────────┐   │
│   │   MemMachine  │    │  Knowledge    │    │    Wave       │   │
│   │   Episodes    │    │    Graph      │    │   Memory      │   │
│   └───────┬───────┘    └───────┬───────┘    └───────┬───────┘   │
│           │                    │                    │           │
│           └────────────────────┼────────────────────┘           │
│                                │                                │
│                                ▼                                │
│                    ┌───────────────────────┐                    │
│                    │   NeuralGraphService  │                    │
│                    └───────────────────────┘                    │
│                                │                                │
│                                ▼                                │
│              ┌─────────────────────────────────┐                │
│              │   LLM (Qwen/External Models)    │                │
│              └─────────────────────────────────┘                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **Heat Score** | Activation level (0-10) tracking node usage frequency |
| **LTP** | Long-Term Potentiation - synaptic strengthening from repeated use |
| **STDP** | Spike-Timing Dependent Plasticity - timing-based learning |
| **Gating** | Non-linear activation function determining edge traversal |
| **Wave Amplitudes** | Multi-dimensional signal strengths (entity, temporal, emotional) |
| **Consolidation** | Memory compression: pruning, merging, promotion, eviction |
| **LSH** | Locality-Sensitive Hashing for approximate nearest neighbor search |

## Appendix B: File Reference

| File | Purpose |
|------|---------|
| `service.py` | Main orchestrator, public API |
| `retriever.py` | Multi-stage retrieval pipeline |
| `query_router.py` | Query analysis and routing |
| `storage.py` | Storage interface and implementation |
| `consolidation.py` | Memory lifecycle management |
| `temporal.py` | Temporal chains and dynamics |
| `gating.py` | Edge activation functions |
| `hierarchy.py` | 4-layer node management |
| `dialogue_linker.py` | Cross-message bindings |
| `tesseract.py` | 4D temporal-spatial mapping |

---

**Document End**

*Generated for MemMachine NeuralGraph v1.0*

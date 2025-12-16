# Neural Graph System Architecture

## Overview

The Neural Graph is a brain-inspired memory retrieval system implementing biologically-plausible mechanisms for memory storage, linking, and retrieval.

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │                    NEURAL GRAPH SERVICE                      │
                        │                      (service.py)                            │
                        │                                                              │
                        │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
                        │  │  Hierarchy   │  │   Temporal   │  │Consolidation │       │
                        │  │   Manager    │  │   Manager    │  │   Manager    │       │
                        │  │(hierarchy.py)│  │(temporal.py) │  │(consolid.py) │       │
                        │  └──────────────┘  └──────────────┘  └──────────────┘       │
                        │                                                              │
                        │  ┌────────────────────────────────────────────────────┐     │
                        │  │              RETRIEVAL LAYER                        │     │
                        │  │  ┌────────────┐ ┌────────────┐ ┌────────────┐      │     │
                        │  │  │  Neural    │ │   Flash    │ │  Electron  │      │     │
                        │  │  │ Retriever  │ │ Retriever  │ │ Retriever  │      │     │
                        │  │  │(retriever) │ │  (flash)   │ │ (electron) │      │     │
                        │  │  └────────────┘ └────────────┘ └────────────┘      │     │
                        │  └────────────────────────────────────────────────────┘     │
                        │                           │                                  │
                        │                           ▼                                  │
                        │  ┌────────────────────────────────────────────────────┐     │
                        │  │                   STORAGE LAYER                     │     │
                        │  │               (storage.py + lsh.py)                 │     │
                        │  │  ┌────────────┐ ┌────────────┐ ┌────────────┐      │     │
                        │  │  │   Nodes    │ │   Edges    │ │  LSH Index │      │     │
                        │  │  │  (Memory)  │ │  (Links)   │ │  (Vector)  │      │     │
                        │  │  └────────────┘ └────────────┘ └────────────┘      │     │
                        │  └────────────────────────────────────────────────────┘     │
                        └─────────────────────────────────────────────────────────────┘
                                                    │
                        ┌───────────────────────────┼───────────────────────────┐
                        │                           │                           │
                        ▼                           ▼                           ▼
              ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
              │    TESSERACT    │       │  DIALOGUE LINKER│       │  QUERY ROUTER   │
              │  (tesseract.py) │       │(dialogue_linker)│       │ (query_router)  │
              │                 │       │                 │       │                 │
              │  4D Multi-Store │       │ Cross-Message   │       │ Query Analysis  │
              │  Brain Memory   │       │ Bindings (Q+A)  │       │ & Pre-filtering │
              └─────────────────┘       └─────────────────┘       └─────────────────┘
```

---

## Core Data Types (data_types.py)

### Node Hierarchy (4 Layers)

```
Layer 3: PERSONA     ───  Stable user model (long-term identity)
            │
Layer 2: TOPIC       ───  Semantic clusters (abstract concepts)
            │
Layer 1: EPISODE     ───  Session groupings (conversation segments)
            │
Layer 0: MESSAGE     ───  Raw messages (individual memories)
            │
Layer 4: TEMPORAL    ───  Time anchors (date/time nodes)
```

### Edge Types

| Edge Type | Purpose | Gating Behavior |
|-----------|---------|-----------------|
| `TEMPORAL` | Time sequence links | Recency-biased decay |
| `SEMANTIC` | Content similarity | Hard similarity threshold |
| `ENTITY` | Shared entity references | Co-occurrence weighted |
| `CAUSAL` | Cause-effect relationships | Strict explicit links |
| `HIERARCHY` | Layer transitions (up/down) | Promotion-based |
| `CO_ACTIVATION` | Hebbian "fire together" | LTP-based learning |

### Neural Dynamics

```
NeuralNode:
├── activation_level    [0, 1]     # Current firing state
├── heat_score         [0, 10]     # Consolidation priority
├── refractory_until   datetime    # Post-fire cooldown
├── wave_amplitudes    dict        # 9D signal vector
└── entity_ids         list        # Knowledge graph links

NeuralEdge:
├── base_weight        [0, 1]      # Connection strength
├── sign               +1/-1       # Excitatory/Inhibitory
├── ltp_boost          [1, 3]      # Long-term potentiation
├── gate_threshold     [0, 1]      # Activation gate
└── propagation_delay  ms          # Signal delay
```

---

## Component Details

### 1. Storage Layer (storage.py)

```
┌──────────────────────────────────────────────────────────────┐
│                  InMemoryNeuralGraphStorage                   │
├──────────────────────────────────────────────────────────────┤
│  _nodes: Dict[node_id, NeuralNode]                           │
│  _edges: Dict[edge_id, NeuralEdge]                           │
│  _session_index: Dict[session_key, Set[node_id]]             │
│  _entity_index: Dict[entity_id, Set[node_id]]                │
│  _lsh_index: RandomProjectionLSH (for fast vector search)    │
├──────────────────────────────────────────────────────────────┤
│  + save_node(node)        + get_nodes_by_session(key)        │
│  + save_edge(edge)        + find_nodes_by_entities(ids)      │
│  + vector_search(emb)     + get_edge_between(src, tgt, type) │
└──────────────────────────────────────────────────────────────┘
```

### 2. Gating System (gating.py)

Implements Image 3: Non-linear activation gating.

```
                    Input Signal
                         │
                         ▼
               ┌─────────────────┐
               │  EdgeGateRegistry│
               │                  │
               │  TEMPORAL ──► RecencyGate(decay=30d)
               │  SEMANTIC ──► ThresholdGate(τ=0.5)
               │  ENTITY   ──► CooccurrenceGate
               │  CAUSAL   ──► StrictGate(confidence)
               └─────────────────┘
                         │
                         ▼
                 Sigmoid Gating
                 f(x) = 1/(1 + e^(-k(x-τ)))
                         │
                         ▼
                   Gated Output
```

### 3. Hierarchy Manager (hierarchy.py)

Implements Image 2: Deep 4-layer network.

```
create_message_node(content, embedding, wave_amplitudes)
        │
        ▼
auto_segment_to_episodes(session_key)  ← Groups messages into episodes
        │
        ▼
cluster_to_topics(session_key)         ← Clusters episodes into topics
        │
        ▼
extract_persona_facets(session_key)    ← Extracts stable persona traits
```

### 4. Temporal Manager (temporal.py)

Implements Image 4: Biological neuron dynamics.

```
┌─────────────────────────────────────────────────────────────┐
│                    Temporal Dynamics                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  STDP (Spike-Timing Dependent Plasticity):                  │
│  ─────────────────────────────────────────                  │
│  • Pre-synaptic fires BEFORE post → Strengthen (LTP)        │
│  • Pre-synaptic fires AFTER post  → Weaken (LTD)            │
│                                                              │
│        ΔW = A+ * exp(-Δt/τ+)  if Δt > 0  (LTP)             │
│        ΔW = A- * exp(Δt/τ-)   if Δt < 0  (LTD)             │
│                                                              │
│  Temporal Chain:                                             │
│  ───────────────                                             │
│  msg1 ──TEMPORAL──► msg2 ──TEMPORAL──► msg3                 │
│   │                   │                   │                  │
│   └───────────────────┴───────────────────┘                 │
│           weight decays with distance                        │
│                                                              │
│  Refractory Period:                                          │
│  ──────────────────                                          │
│  After activation, node enters cooldown (50-500ms by layer) │
│  Prevents retrieval loops and runaway activation            │
└─────────────────────────────────────────────────────────────┘
```

### 5. Consolidation Manager (consolidation.py)

Implements Image 5: Compression/pruning.

```
┌─────────────────────────────────────────────────────────────┐
│                   Consolidation Cycle                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. HEAT DECAY                                               │
│     heat *= exp(-days_since_activation / half_life)         │
│                                                              │
│  2. PROMOTION (heat > 2.5)                                   │
│     MESSAGE → EPISODE → TOPIC → PERSONA                     │
│                                                              │
│  3. EVICTION (heat < 0.25)                                   │
│     Mark node as EVICTED, prune weak edges                  │
│                                                              │
│  4. HUB CREATION                                             │
│     Merge highly-connected nodes into summary hubs          │
│                                                              │
│  Heat Formula:                                               │
│  new_heat = base + (activations * 0.25) - decay             │
└─────────────────────────────────────────────────────────────┘
```

---

## Retrieval Architectures

### A. Neural Retriever (retriever.py) - Sequential Multi-Stage

```
Query ──► Stage 1: Vector Search (embedding similarity)
              │
              ▼
          Stage 2: Entity Expansion (follow ENTITY edges)
              │
              ▼
          Stage 3: Temporal Walk (follow TEMPORAL chains)
              │
              ▼
          Stage 4: Pattern Completion (CA3-inspired)
              │
              ▼
          Wavefront Propagation (spread activation)
              │
              ▼
          Final Ranking
```

### B. Flash Retriever (flash_retriever.py) - Parallel Resonance

```
        Query
          │
    ┌─────┼─────┐
    ▼     ▼     ▼
 Vector Entity Temporal
 Search  Match  Match
    │     │     │
    └─────┼─────┘
          ▼
    Parallel Resonance
    (all signals at once)
          │
          ▼
    Interference Scoring
    (constructive/destructive)
          │
          ▼
    Final Ranking
```

### C. Electron Retriever (electron.py) - Electrical Simulation

```
                    Query
                      │
                      ▼
            ┌─────────────────┐
            │  Inject Charge  │
            │  at seed nodes  │
            └─────────────────┘
                      │
                      ▼
            ┌─────────────────┐
            │    Propagate    │
            │  via conductance│
            │    (edges)      │
            └─────────────────┘
                      │
                      ▼
            ┌─────────────────┐
            │   Accumulate    │
            │  at each node   │
            └─────────────────┘
                      │
                      ▼
            ┌─────────────────┐
            │   Fire nodes    │
            │  above threshold│
            └─────────────────┘
                      │
                      ▼
                 Results
```

---

## Tesseract: 4D Brain-Inspired Memory (tesseract.py)

The **Tesseract** implements a neuroscience-inspired multi-store architecture:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TESSERACT                                       │
│                         4D Memory Architecture                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌───────────────────┐    ┌───────────────────┐                            │
│   │  TEMPORAL STORE   │    │   ENTITY STORE    │                            │
│   │   (Hippocampus)   │    │   (Neocortex)     │                            │
│   │                   │    │                   │                            │
│   │ "When did X?"     │    │ "What is X?"      │                            │
│   │ Time sequences    │    │ Facts & attributes│                            │
│   │ Relative dates    │    │ Entity binding    │                            │
│   └─────────┬─────────┘    └─────────┬─────────┘                            │
│             │                        │                                       │
│             └────────────┬───────────┘                                       │
│                          │                                                   │
│                    ┌─────▼─────┐                                            │
│                    │  FUSION   │                                            │
│                    │  LAYER    │ ◄── Query type detection weights           │
│                    └─────┬─────┘                                            │
│                          │                                                   │
│             ┌────────────┴───────────┐                                       │
│             │                        │                                       │
│   ┌─────────▼─────────┐    ┌─────────▼─────────┐                            │
│   │ REASONING STORE   │    │ ADVERSARIAL STORE │                            │
│   │(Prefrontal Cortex)│    │ (Orbitofrontal)   │                            │
│   │                   │    │                   │                            │
│   │ "How many X?"     │    │ "X but not Y?"    │                            │
│   │ Multi-hop paths   │    │ Negation handling │                            │
│   │ Aggregation       │    │ Conflict detect   │                            │
│   └───────────────────┘    └───────────────────┘                            │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  Query Type Detection → Fusion Weights:                                      │
│  • TEMPORAL query  → weight temporal_store higher                           │
│  • ENTITY query    → weight entity_store higher                             │
│  • MULTI_HOP query → weight reasoning_store higher                          │
│  • ADVERSARIAL     → weight adversarial_store higher                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Supporting Components

### Pattern Completion (pattern_completion.py)
CA3 hippocampal pattern completion - reconstruct full memory from partial cue.

### Interference Scoring (interference.py)
Handles constructive/destructive interference between memory signals.

### Wavefront Propagation (wavefront.py)
Spreading activation across the graph (like neural signal propagation).

### LSH Index (lsh.py)
Locality-Sensitive Hashing for O(1) approximate nearest neighbor search.

### Dialogue Linker (dialogue_linker.py)
Creates cross-message bindings:
- Q+A pairs as atomic units
- Coreference resolution
- Topic thread linking

### Synonym Hash (synonym_hash.py + en_thesaurus.jsonl)
117K+ word thesaurus for morphological and synonym expansion.

---

## Data Flow: Ingestion

```
Episode/Message
      │
      ▼
┌─────────────────────────────────────┐
│         process_episodes()          │
├─────────────────────────────────────┤
│ 1. Create MESSAGE nodes             │
│ 2. Build TEMPORAL chains            │
│ 3. Create ENTITY edges              │
│ 4. Create SEMANTIC edges            │
│ 5. Create DIALOGUE links            │
│ 6. Update fairness metadata         │
│ 7. Run consolidation check          │
└─────────────────────────────────────┘
      │
      ▼
  Storage Layer
```

## Data Flow: Retrieval

```
Query + Embedding
      │
      ▼
┌─────────────────────────────────────┐
│           retrieve()                │
├─────────────────────────────────────┤
│ 1. Query routing (if enabled)       │
│ 2. Select retriever:                │
│    • Electron (electrical sim)      │
│    • Flash (parallel resonance)     │
│    • Neural (sequential stages)     │
│ 3. Execute retrieval                │
│ 4. Dialogue link expansion          │
│ 5. Apply temporal dynamics (STDP)   │
│ 6. Return ranked results            │
└─────────────────────────────────────┘
      │
      ▼
  (NeuralNode, score)[]
```

---

## File Summary

| File | LOC | Purpose |
|------|-----|---------|
| `service.py` | 1548 | Main orchestration service |
| `tesseract.py` | 1323 | 4D brain-inspired retrieval |
| `retriever.py` | ~1800 | Multi-stage sequential retrieval |
| `electron.py` | ~1500 | Electrical simulation retrieval |
| `storage.py` | ~1000 | Graph storage + indexing |
| `data_types.py` | 680 | Core data structures |
| `flash_retriever.py` | ~800 | Parallel resonance retrieval |
| `dialogue_linker.py` | ~800 | Cross-message linking |
| `consolidation.py` | ~1000 | Memory consolidation |
| `temporal.py` | ~900 | Temporal dynamics (STDP, LTP) |
| `hierarchy.py` | ~800 | 4-layer node hierarchy |
| `gating.py` | ~600 | Edge gating functions |
| `pattern_completion.py` | ~700 | CA3 pattern completion |
| `interference.py` | ~300 | Signal interference |
| `wavefront.py` | ~500 | Spreading activation |
| `lsh.py` | ~500 | Vector indexing |
| `query_router.py` | ~600 | Query analysis |
| `prompts.py` | ~200 | LLM prompt templates |
| `synonym_hash.py` | ~300 | Synonym expansion |

**Total: ~21 files, ~15,000+ LOC**

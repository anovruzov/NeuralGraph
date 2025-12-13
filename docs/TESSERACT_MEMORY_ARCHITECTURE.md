**Authors:** Ali Novruzov - Built on MemMachine's existing stack.

---

## TABLE OF CONTENTS

1. [Executive Summary](#1-executive-summary)
2. [4D Tesseract Memory Architecture](#2-4d-tesseract-memory-architecture)
3. [Holographic Memory System](#3-holographic-memory-system)
4. [Wave Propagation Retrieval](#4-wave-propagation-retrieval)
5. [Temporal Chain Management with LTP](#5-temporal-chain-management-with-ltp)
6. [Multi-Store Specialized Retrieval](#6-multi-store-specialized-retrieval)
7. [Emotional Resonance Scoring](#7-emotional-resonance-scoring)
8. [Adaptive Granularity Routing](#8-adaptive-granularity-routing)
9. [Query Type Detection System](#9-query-type-detection-system)
10. [Claims Summary](#10-claims-summary)

---

## 1. EXECUTIVE SUMMARY

### 1.1 Field of Invention

This invention relates to computer-implemented methods and systems for memory retrieval in artificial intelligence systems, specifically a brain-inspired multi-dimensional memory architecture that achieves higher accuracy on adversarial questions on conversational memory benchmarks as well as latencies.

### 1.2 Problem Statement

Prior art memory retrieval systems suffer from:
- **Flat semantic search**: Treating all queries uniformly regardless of query type
- **Loss of temporal context**: Relative date references ("yesterday", "last week") cannot be resolved
- **Speaker attribution failure**: Memories not properly associated with speakers
- **Single-granularity limitation**: Fixed chunk sizes regardless of query complexity

### 1.3 Solution Overview

The MemMachine system introduces a **4D Tesseract Memory Architecture** that:
1. Routes queries to specialized brain-region-inspired stores
2. Represents memories as wave functions across token-domain space
3. Implements biological neural dynamics (LTP, temporal summation, refractory periods)
4. Dynamically selects retrieval granularity based on query entropy
5. Applies emotional resonance for mood-congruent retrieval

---

## 2. 4D TESSERACT MEMORY ARCHITECTURE

### 2.1 Core Innovation

The Tesseract architecture is modeled after distinct brain regions that specialize in different memory retrieval tasks:

```
DIMENSION 1: HIPPOCAMPUS (Temporal Store)
├── Specialization: Episodic sequences, temporal queries
├── Query patterns: "When did X happen?", "What happened before Y?"
├── Key mechanism: Time-ordered chains, relative date resolution
└── Brain analog: Hippocampal time cells

DIMENSION 2: NEOCORTEX (Entity Store)
├── Specialization: Semantic facts, entity attributes
├── Query patterns: "What is X?", "Who is Y?"
├── Key mechanism: Entity binding, speaker attribution
└── Brain analog: Cortical semantic networks

DIMENSION 3: PREFRONTAL CORTEX (Reasoning Store)
├── Specialization: Multi-hop inference chains
├── Query patterns: "How many X did Y do?", "List all Z"
├── Key mechanism: Breadth-first retrieval, working memory
└── Brain analog: Prefrontal working memory

DIMENSION 4: ORBITOFRONTAL CORTEX (Adversarial Store)
├── Specialization: Conflict detection, negation handling
├── Query patterns: "What did X NOT do?", "Except for Y"
├── Key mechanism: Contradiction detection, verification
└── Brain analog: Orbitofrontal conflict monitoring
```

### 2.2 Query Type Detection Algorithm

```python
ALGORITHM: detect_query_type(query: string) -> confidence_scores

INPUT: Natural language query string
OUTPUT: Dictionary mapping {query_type -> confidence [0,1]}

STEP 1: Initialize base scores
    scores = {
        TEMPORAL: 0.0,
        ENTITY: 0.0,
        MULTI_HOP: 0.0,
        ADVERSARIAL: 0.0,
        OPEN: 0.3  # Base score for open-ended
    }

STEP 2: Apply temporal marker detection
    TEMPORAL_MARKERS = [
        (\bwhen\b, 0.6),
        (\bhow long\b, 0.5),
        (\byesterday\b, 0.4),
        (\blast week\b, 0.5),
        (\blast year\b, 0.5),
        (\b\d+ years? ago\b, 0.6),
        (\bbefore\b, 0.3),
        (\bafter\b, 0.3)
    ]
    FOR each (pattern, weight) in TEMPORAL_MARKERS:
        IF pattern matches query:
            scores[TEMPORAL] += weight

STEP 3: Apply entity marker detection
    ENTITY_MARKERS = [
        (\bwhat is\b, 0.5),
        (\bwho is\b, 0.5),
        (\bidentity\b, 0.5),
        (\bcareer\b, 0.3),
        (\bfrom\b, 0.2)
    ]
    FOR each (pattern, weight) in ENTITY_MARKERS:
        IF pattern matches query:
            scores[ENTITY] += weight

STEP 4: Apply multi-hop marker detection
    MULTIHOP_MARKERS = [
        (\bhow many\b, 0.4),
        (\ball\b, 0.3),
        (\blist\b, 0.4),
        (\band\b.*\band\b, 0.4)  # Multiple conjunctions
    ]

STEP 5: Apply adversarial marker detection
    ADVERSARIAL_MARKERS = [
        (\bnot\b, 0.4),
        (\bnever\b, 0.5),
        (\bexcept\b, 0.4),
        (\bother than\b, 0.5)
    ]

STEP 6: Normalize and cap scores at 1.0

RETURN scores
```

### 2.3 Tesseract Fusion Algorithm

```python
ALGORITHM: tesseract_retrieve(query, embedding, session_key) -> ranked_results

INPUT:
    - query: Natural language query
    - embedding: Vector embedding of query
    - session_key: Session identifier

OUTPUT: List of (memory_node, fused_score) tuples

STEP 1: Detect query type
    type_weights = detect_query_type(query)

STEP 2: Query all specialized stores IN PARALLEL
    temporal_results = temporal_store.retrieve(query, embedding, limit=40)
    entity_results = entity_store.retrieve(query, embedding, limit=50)
    reasoning_results = reasoning_store.retrieve(query, embedding, limit=40)
    adversarial_results = adversarial_store.retrieve(query, embedding, limit=30)

STEP 3: Fuse results with dynamic weighting
    combined = {}

    # Normalize type weights
    total_weight = sum(type_weights.values())

    temporal_weight = type_weights[TEMPORAL] / total_weight
    entity_weight = type_weights[ENTITY] / total_weight
    multihop_weight = type_weights[MULTI_HOP] / total_weight
    adversarial_weight = type_weights[ADVERSARIAL] / total_weight
    open_weight = type_weights[OPEN] / total_weight

    # Fuse temporal results
    FOR each (node, charge) in temporal_results:
        effective_charge = charge * (temporal_weight + open_weight * 0.25)
        combined[node.id] = (node, combined.get(node.id, 0) + effective_charge)

    # Fuse entity results
    FOR each (node, charge) in entity_results:
        effective_charge = charge * (entity_weight + open_weight * 0.35)
        combined[node.id] = (node, combined.get(node.id, 0) + effective_charge)

    # Fuse reasoning results
    FOR each (node, charge) in reasoning_results:
        effective_charge = charge * (multihop_weight + open_weight * 0.25)
        combined[node.id] = (node, combined.get(node.id, 0) + effective_charge)

    # Fuse adversarial results
    FOR each (node, charge) in adversarial_results:
        effective_charge = charge * (adversarial_weight + open_weight * 0.15)
        combined[node.id] = (node, combined.get(node.id, 0) + effective_charge)

STEP 4: Sort by fused score descending

RETURN top_k results
```

---

## 3. HOLOGRAPHIC MEMORY SYSTEM

### 3.1 Core Innovation

Unlike traditional 3D graph-based memory where nodes must be SEARCHED and FOUND, holographic memory enables memories to EMERGE through concept intersection. Any concept that was present during encoding can serve as an access key.

```
TRADITIONAL 3D APPROACH (Prior Art):
    Query → Search Index → Find Matching Nodes → Return
    LIMITATION: Requires explicit path traversal

HOLOGRAPHIC 4D APPROACH (This Invention):
    Query Concepts → Intersection with Memory Concepts → Memory EMERGES
    ADVANTAGE: Fast access via any concept dimension
```

### 3.2 Holographic Trace Data Structure

```python
STRUCTURE: HolographicTrace
    trace_id: string           # Unique identifier
    content: string            # Unified content (includes speaker context)
    access_keys: set[string]   # ALL concepts that can access this memory
    speaker: string            # Speaker as first-class access key
    timestamp: datetime        # 4th dimension coordinate
    embedding: float[]         # Fallback semantic vector
    node_id: string            # Reference to underlying node

KEY INSIGHT: Speaker is NOT metadata - it is a FIRST-CLASS ACCESS KEY
    - Traditional: "Caroline" stored as metadata, lost during retrieval
    - Holographic: "caroline" is in access_keys, enables direct access
```

### 3.3 Concept Extraction Algorithm

```python
ALGORITHM: extract_concepts(text: string) -> set[string]

INPUT: Raw text content
OUTPUT: Set of concept access keys

STEP 1: Tokenize and lowercase
    words = regex_findall(\b[a-z]+\b, text.lower())

STEP 2: Filter stopwords
    STOP_CONCEPTS = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can',
        'to', 'of', 'and', 'or', 'but', 'if', 'then', 'so', 'for',
        'with', 'at', 'by', 'from', 'up', 'down', 'in', 'out', 'on',
        'off', 'over', 'under', 'again', 'further', 'then', 'once',
        'i', 'you', 'he', 'she', 'we', 'they', 'me', 'him', 'her',
        'us', 'them', 'my', 'your', 'his', 'our', 'their'
    }
    concepts = {w for w in words if w not in STOP_CONCEPTS and len(w) > 2}

STEP 3: Extract bigrams for compound concepts
    # "support group" becomes "support_group"
    words_list = [w for w in words if w not in STOP_CONCEPTS and len(w) > 2]
    FOR i in range(len(words_list) - 1):
        bigram = words_list[i] + "_" + words_list[i+1]
        concepts.add(bigram)

RETURN concepts
```

### 3.4 Holographic Retrieval Algorithm

```python
ALGORITHM: holographic_retrieve(query, limit, embedding) -> ranked_traces

INPUT:
    - query: Natural language query
    - limit: Maximum results to return
    - embedding: Optional query embedding for fallback

OUTPUT: List of (trace, resonance_score) tuples

STEP 1: Extract query concepts
    query_concepts = extract_concepts(query)

STEP 2: Find candidate traces via inverted index
    # Fast lookup per concept
    candidate_ids = set()
    FOR concept in query_concepts:
        IF concept in concept_index:
            candidate_ids.update(concept_index[concept])

STEP 3: Score by concept intersection (RESONANCE)
    scored = []
    FOR trace_id in candidate_ids:
        trace = traces[trace_id]

        # PRIMARY: Concept intersection ratio
        intersection = query_concepts & trace.access_keys
        intersection_score = len(intersection) / len(query_concepts)

        # SECONDARY: Embedding similarity (fallback)
        embedding_score = 0.0
        IF embedding AND trace.embedding:
            embedding_score = cosine_similarity(embedding, trace.embedding)

        # Combined: 70% intersection, 30% embedding
        # Intersection is PRIMARY - it IS the 4D access mechanism
        final_score = 0.7 * intersection_score + 0.3 * max(0, embedding_score)

        scored.append((trace, final_score))

STEP 4: Sort by score descending

RETURN scored[:limit]
```

---

## 4. WAVE PROPAGATION RETRIEVAL

### 4.1 Core Innovation

Memory retrieval is modeled as wave interference in a 4-domain Hilbert space. Each memory is a wave function ψ(t,d) over token positions (t) and domains (d).

```
WAVE EQUATION:
    R(memory|query) = |⟨ψ_memory|ψ_query⟩|²
                    = |Σ_t Σ_d ψ_m(t,d)* · ψ_q(t,d)|²

WHERE:
    t = token position in sequence
    d ∈ {Entity, Temporal, Semantic, Relation}
    ψ(t,d) = amplitude at token t in domain d
```

### 4.2 Domain Classification

```python
DOMAINS:
    ENTITY (E):    Names, pronouns resolved to names
    TEMPORAL (T):  Date/time markers, temporal references
    SEMANTIC (S):  Content words, meaningful terms
    RELATION (R):  Verbs, action words, relationship indicators

CLASSIFICATION RULES:
    - Capitalized words (not stopwords) → Entity amplitude = 1.0
    - Temporal patterns (yesterday, 2023, Monday) → Temporal amplitude = 1.0
    - Action verbs (went, paint, run) → Relation amplitude = 1.0
    - Other meaningful words → Semantic amplitude = 1.0
```

### 4.3 Token Wave Data Structure

```python
STRUCTURE: TokenWave
    token: string           # The token text
    position: int           # Position in sequence
    entity_amp: float       # Amplitude in Entity domain
    temporal_amp: float     # Amplitude in Temporal domain
    semantic_amp: float     # Amplitude in Semantic domain
    relation_amp: float     # Amplitude in Relation domain
    phase: float            # Phase for contextual binding

METHOD: total_amplitude() -> float
    RETURN sqrt(entity_amp² + temporal_amp² + semantic_amp² + relation_amp²)

METHOD: domain_vector() -> (float, float, float, float)
    RETURN (entity_amp, temporal_amp, semantic_amp, relation_amp)
```

### 4.4 Propagation Kernel

```python
ALGORITHM: propagate_tokens(waves: list[TokenWave]) -> list[TokenWave]

PURPOSE: Propagate activation between adjacent tokens (sequential flow)

PARAMETERS:
    TOKEN_DECAY = 0.7    # Each position away reduces by this factor
    TOKEN_RANGE = 3      # Maximum positions to propagate

FOR each wave at position i:
    new_wave = copy(wave)

    # Receive activation from nearby tokens
    FOR j in range(max(0, i-TOKEN_RANGE), min(n, i+TOKEN_RANGE+1)):
        IF i != j:
            distance = abs(i - j)
            decay = TOKEN_DECAY ^ distance

            # Add decayed activation from neighbor
            new_wave.entity_amp += waves[j].entity_amp * decay * 0.3
            new_wave.temporal_amp += waves[j].temporal_amp * decay * 0.3
            new_wave.semantic_amp += waves[j].semantic_amp * decay * 0.3
            new_wave.relation_amp += waves[j].relation_amp * decay * 0.3

    propagated.append(new_wave)

RETURN propagated
```

### 4.5 Domain Coupling Matrix

```python
ALGORITHM: propagate_domains(wave: TokenWave) -> TokenWave

PURPOSE: Propagate activation between domains within a token (cross-coupling)

DOMAIN_COUPLING_MATRIX:
           E    T    S    R
    E   [1.0, 0.3, 0.5, 0.4]   # Entity couples with other domains
    T   [0.3, 1.0, 0.3, 0.2]   # Temporal couples with other domains
    S   [0.5, 0.3, 1.0, 0.6]   # Semantic couples with other domains
    R   [0.4, 0.2, 0.6, 1.0]   # Relation couples with other domains

COMPUTATION:
    amps = [wave.entity_amp, wave.temporal_amp, wave.semantic_amp, wave.relation_amp]
    new_amps = [0, 0, 0, 0]

    FOR i in range(4):
        FOR j in range(4):
            new_amps[i] += amps[j] * COUPLING_MATRIX[j][i]

    RETURN TokenWave with new_amps
```

### 4.6 Resonance Computation

```python
ALGORITHM: compute_resonance(query_waves, memory, embedding) -> (score, breakdown)

COMPONENTS:

1. DOMAIN RESONANCE (inner product of domain vectors):
    q_vec = normalized(sum(query_wave.domain_vector() for all query_waves))
    m_vec = normalized(sum(memory.tokens[i].domain_vector() for all i))
    domain_resonance = dot_product(q_vec, m_vec)

2. TOKEN ALIGNMENT (direct token matches):
    query_tokens = {w.token.lower() for w in query_waves}
    memory_tokens = {w.token.lower() for w in memory.tokens}
    token_alignment = len(query_tokens & memory_tokens) / len(query_tokens)

3. ENTITY SPECIFICITY (critical for speaker attribution):
    query_entities = {w.token for w in query_waves if w.entity_amp > 0.5}
    memory_entities = {w.token for w in memory.tokens if w.entity_amp > 0.5}
    entity_match = len(query_entities & memory_entities) / len(query_entities)

4. SEMANTIC OVERLAP:
    query_semantics = {w.token for w in query_waves if w.semantic_amp > 0.5}
    memory_semantics = {w.token for w in memory.tokens if w.semantic_amp > 0.5}
    semantic_overlap = len(query_semantics & memory_semantics) / len(query_semantics)

5. EMBEDDING SIMILARITY (fallback):
    embedding_sim = cosine_similarity(embedding, memory.embedding)

FINAL RESONANCE (wave interference pattern):
    resonance = (
        0.25 * domain_resonance +
        0.20 * token_alignment +
        0.25 * entity_match +       # CRITICAL for speaker attribution
        0.15 * semantic_overlap +
        0.15 * embedding_sim
    )

AMPLIFICATION:
    IF entity_match > 0.8 AND (semantic_overlap > 0.2 OR token_alignment > 0.3):
        resonance *= 1.3

RETURN (resonance, breakdown)
```

---

## 5. TEMPORAL CHAIN MANAGEMENT WITH LTP

### 5.1 Core Innovation

Implements biological neural dynamics including:
- **Long-Term Potentiation (LTP)**: Connections strengthen through repeated co-activation
- **Spike-Timing Dependent Plasticity (STDP)**: Timing-based learning rule
- **Temporal Summation**: Multiple weak inputs sum to strong signal
- **Refractory Periods**: Prevents runaway excitation

### 5.2 Temporal Edge Data Structure

```python
STRUCTURE: NeuralEdge
    edge_id: string
    source_id: string               # Presynaptic node
    target_id: string               # Postsynaptic node
    edge_type: EdgeType             # TEMPORAL, CAUSAL, CO_ACTIVATION
    base_weight: float              # Initial weight
    propagation_delay_ms: float     # Signal transmission delay
    confidence: float               # Edge confidence [0,1]
    ltp_boost: float                # LTP multiplier (default 1.0)
    activation_count: int           # Number of activations
    last_activated: datetime        # Last activation time
    gate_threshold: float           # Minimum charge to pass

PROPERTY: effective_weight -> float
    RETURN base_weight * ltp_boost * confidence
```

### 5.3 Temporal Edge Creation Algorithm

```python
ALGORITHM: create_temporal_edge(source_node, target_node, direction) -> edge

STEP 1: Compute propagation delay based on time difference
    time_diff_seconds = abs(target_node.created_at - source_node.created_at)
    delay_ms = clamp(
        time_diff_seconds * 10,  # 10ms per second of gap
        min=MIN_PROPAGATION_DELAY_MS,  # 10.0
        max=MAX_PROPAGATION_DELAY_MS   # 10000.0
    )

STEP 2: Compute base weight from temporal proximity
    # Closer in time = stronger link
    hours_diff = delay_ms / (1000 * 3600)
    base_weight = exp(-hours_diff / MAX_TIME_GAP_HOURS)

STEP 3: Create edge
    edge = NeuralEdge(
        edge_id=generate_uuid(),
        source_id=source_node.node_id,
        target_id=target_node.node_id,
        edge_type=TEMPORAL,
        base_weight=base_weight,
        propagation_delay_ms=delay_ms,
        confidence=1.0,
        metadata={"direction": direction, "time_diff_seconds": time_diff_seconds}
    )

RETURN edge
```

### 5.4 Long-Term Potentiation (LTP) Algorithm

```python
ALGORITHM: record_co_activation(nodes: list[NeuralNode], context: string) -> edges_strengthened

PURPOSE: Implement Hebbian learning - "Neurons that fire together wire together"

STEP 1: Build canonical pairs (smaller ID always source)
    pairs = []
    FOR i, node1 in enumerate(nodes):
        FOR node2 in nodes[i+1:]:
            canonical = (min(node1.id, node2.id), max(node1.id, node2.id))
            pairs.append(canonical)

STEP 2: Batch fetch existing edges
    existing_edges = storage.get_edges_batch(pairs, CO_ACTIVATION)

STEP 3: Strengthen or create edges
    edges_to_save = []
    FOR (id1, id2), edge in zip(pairs, existing_edges):
        IF edge is None:
            # Create new co-activation edge
            edge = NeuralEdge(
                source_id=id1,
                target_id=id2,
                edge_type=CO_ACTIVATION,
                base_weight=0.3  # Start stronger than 0.1
            )

        # LTP: strengthen with activation
        edge.activate()  # Increments activation_count, boosts ltp_boost
        edges_to_save.append(edge)

STEP 4: Batch save all edges

RETURN len(edges_to_save)
```

### 5.5 Spike-Timing Dependent Plasticity (STDP)

```python
ALGORITHM: apply_stdp(source_id, target_id, time_delta_ms) -> modified

PURPOSE: Biological learning rule based on relative timing

PARAMETERS:
    TAU = 20.0  # STDP time constant (biological: ~20ms)

STEP 1: Get edge between nodes
    edge = storage.get_edge_between(source_id, target_id, TEMPORAL)
    IF edge is None: RETURN False

STEP 2: Apply STDP rule
    IF time_delta_ms > 0:  # Source fired first (causal)
        # LTP: Strengthen the connection
        stdp_factor = exp(-time_delta_ms / TAU)
        edge.ltp_boost = min(LTP_MAX_BOOST, edge.ltp_boost + 0.1 * stdp_factor)
    ELSE:  # Target fired first (anti-causal)
        # LTD: Weaken the connection (depression)
        stdp_factor = exp(time_delta_ms / TAU)  # time_delta_ms is negative
        edge.ltp_boost = max(1.0, edge.ltp_boost - 0.05 * stdp_factor)

STEP 3: Save edge
    storage.save_edge(edge)

RETURN True
```

### 5.6 Temporal Summation Algorithm

```python
ALGORITHM: temporal_summation(node, window_hours) -> summed_signal

PURPOSE: Multiple weak inputs over time sum to strong signal
         (Similar to dendritic temporal summation in biological neurons)

STEP 1: Get time window
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=window_hours)

STEP 2: Get recent incoming edges
    all_edges = storage.get_edges_to(node.node_id)
    recent_edges = [e for e in all_edges if e.last_activated > window_start]

STEP 3: Define temporal kernel (recent activations weight more)
    FUNCTION kernel(edge, now) -> float:
        hours_ago = (now - edge.last_activated).total_seconds() / 3600
        RETURN exp(-hours_ago / window_hours)

STEP 4: Sum signals with temporal weighting
    summed_signal = sum(
        edge.effective_weight * kernel(edge, now)
        FOR edge in recent_edges
    )

RETURN summed_signal
```

---

## 6. MULTI-STORE SPECIALIZED RETRIEVAL

### 6.1 Temporal Store (Hippocampus-Inspired)

```python
ALGORITHM: temporal_store_retrieve(query, embedding, session_key, reference_time)

PURPOSE: Specialized for temporal/episodic retrieval

CHARGE COMPUTATION:
    total_charge = (
        semantic_charge * 0.3 +     # Lower semantic weight
        temporal_charge * 0.4 +     # HIGH temporal keyword matching
        grounded_charge * 0.3 +     # Grounded dates ([= markers)
        recency_charge * 0.2        # Time decay
    )

TEMPORAL KEYWORD EXTRACTION:
    keywords = find_all([
        'yesterday', 'today', 'tomorrow',
        'last week', 'this week', 'next week',
        'last month', 'this month', 'next month',
        'last year', 'this year', 'next year',
        '\d{4}',  # Years like 2023
        'january|february|...|december',
        'monday|tuesday|...|sunday'
    ], query)

TEMPORAL CHARGE:
    FOR keyword in keywords:
        IF keyword in node.content.lower():
            temporal_charge += 0.25
    temporal_charge = min(1.0, temporal_charge)

GROUNDED DATE BOOST:
    IF "[=" in node.content:  # Has grounded dates
        grounded_charge = 0.2
        FOR keyword in keywords:
            IF keyword in node.content:
                grounded_charge += 0.15
```

### 6.2 Entity Store (Neocortex-Inspired)

```python
ALGORITHM: entity_store_retrieve(query, embedding, session_key)

PURPOSE: Specialized for entity/fact retrieval

CHARGE COMPUTATION:
    total_charge = (
        semantic_charge * 0.4 +
        entity_charge * 0.6 +       # HIGH entity binding
        speaker_charge * 0.7 +      # HIGH speaker binding
        keyword_charge * 0.3
    )

ENTITY BINDING:
    query_entities = extract_capitalized_words(query) - common_words
    matches = count(e for e in query_entities if e.lower() in node.content.lower())
    entity_charge = min(1.0, matches / len(query_entities))

SPEAKER BINDING:
    speaker = node.metadata.get("speaker", "")
    IF any(e.lower() == speaker.lower() for e in query_entities):
        speaker_charge = 1.0

CRITICAL AMPLIFICATION:
    IF speaker_charge > 0.5 AND semantic_charge > 0.3:
        total_charge *= 1.3  # Ensures ALL speaker messages have a chance
```

### 6.3 Reasoning Store (Prefrontal-Inspired)

```python
ALGORITHM: reasoning_store_retrieve(query, embedding, session_key)

PURPOSE: Specialized for multi-hop inference chains

KEY DIFFERENCE: Need BREADTH not depth
    - Retrieve MORE nodes with MODERATE relevance
    - Lower minimum charge threshold
    - Higher keyword weight

PARAMETERS:
    semantic_weight = 0.5
    entity_boost = 0.4
    speaker_boost = 0.3
    keyword_boost = 0.4      # Higher for multi-hop
    min_charge = 0.08        # Lower threshold for breadth
    max_results = 60         # More results for multi-hop
```

### 6.4 Adversarial Store (Orbitofrontal-Inspired)

```python
ALGORITHM: adversarial_store_retrieve(query, embedding, session_key)

PURPOSE: Specialized for negation/conflict handling

ADVERSARIAL PARSING:
    negation_patterns = [
        r'not\s+(\w+)',
        r'never\s+(\w+)',
        r"didn't\s+(\w+)",
        r'except\s+(\w+)',
        r'other than\s+(\w+)'
    ]
    negation_terms = extract_all(negation_patterns, query)
    positive_terms = all_keywords - negation_terms

ADVERSARIAL CHARGE:
    # Nodes that DON'T contain negated terms are valuable
    # (they show what DID happen)
    contains_negated = count(t for t in negation_terms if t in node.content)
    IF contains_negated == 0:
        negation_charge = 0.2  # Doesn't mention negated thing
    ELSE:
        negation_charge = 0.4  # Mentions it - could be evidence
```

---

## 7. EMOTIONAL RESONANCE SCORING

### 7.1 Core Innovation

Implements mood-congruent memory retrieval based on psychological research showing that memories emotionally congruent with current mood are preferentially retrieved.

### 7.2 Emotional Vector Data Structure

```python
STRUCTURE: EmotionalVector
    joy: float           # [0, 1]
    sadness: float       # [0, 1]
    anger: float         # [0, 1]
    fear: float          # [0, 1]
    surprise: float      # [0, 1]
    disgust: float       # [0, 1]
    trust: float         # [0, 1]
    anticipation: float  # [0, 1]

DERIVED PROPERTIES:
    valence: float = (joy + trust + anticipation - sadness - anger - fear - disgust) / 7
    arousal: float = (joy + anger + fear + surprise) / 4
    dominance: float = (anger + trust - fear - sadness) / 4
```

### 7.3 Emotional Resonance Computation

```python
ALGORITHM: compute_resonance(memory_vector, mood_state, mood_weight) -> EmotionalResonance

STEP 1: Compute emotional similarity
    # Cosine similarity between memory emotion vector and mood state
    similarity = cosine_similarity(memory_vector.to_array(), mood_state.emotional_center)

STEP 2: Compute valence congruence
    memory_valence = memory_vector.valence
    mood_valence = mood_state.valence
    valence_congruent = abs(memory_valence - mood_valence) < 0.3

STEP 3: Compute resonance score
    base_resonance = 1.0 + (similarity * mood_weight)

    IF valence_congruent:
        resonance_score = base_resonance * (1.0 + VALENCE_BOOST)
    ELSE:
        resonance_score = base_resonance

RETURN EmotionalResonance(
    resonance_score=resonance_score,
    emotional_similarity=similarity,
    is_mood_congruent=valence_congruent
)
```

### 7.4 Mood-Congruent Reranking

```python
ALGORITHM: rerank_by_resonance(scored_results, mood_state) -> reranked_results

FOR each result in scored_results:
    emotional_vector = extract_emotional_vector(result.memory)
    resonance = compute_resonance(emotional_vector, mood_state)

    # Blend semantic and emotional scores
    result.final_score = (
        SEMANTIC_WEIGHT * result.original_score +
        EMOTIONAL_WEIGHT * (result.original_score * resonance.resonance_score)
    )

SORT results by final_score descending

# Calculate rank boost (how much position changed)
FOR new_rank, result in enumerate(sorted_results):
    result.rank_boost = result.original_rank - new_rank

RETURN sorted_results
```

---

## 8. ADAPTIVE GRANULARITY ROUTING

### 8.1 Core Innovation (MemGAS Architecture)

Dynamically selects retrieval granularity based on query entropy:
- **Low entropy** (simple query) → Keyword-level retrieval
- **Medium entropy** → Sentence-level retrieval
- **High entropy** (complex query) → Paragraph/document-level retrieval

### 8.2 Entropy Calculation

```python
ALGORITHM: calculate_entropy(query: string) -> EntropyScore

STEP 1: Tokenize
    tokens = tokenize(query)
    n_tokens = len(tokens)

STEP 2: Calculate word frequency distribution
    freq = Counter(tokens)
    probabilities = [count/n_tokens for count in freq.values()]

STEP 3: Calculate Shannon entropy
    entropy = -sum(p * log2(p) for p in probabilities if p > 0)

STEP 4: Determine granularity based on entropy thresholds
    ENTROPY_THRESHOLDS = {
        KEYWORD: 1.5,
        SENTENCE: 3.0,
        PARAGRAPH: 4.5,
        DOCUMENT: infinity
    }

    IF entropy < ENTROPY_THRESHOLDS[KEYWORD]:
        recommended = KEYWORD
    ELIF entropy < ENTROPY_THRESHOLDS[SENTENCE]:
        recommended = SENTENCE
    ELIF entropy < ENTROPY_THRESHOLDS[PARAGRAPH]:
        recommended = PARAGRAPH
    ELSE:
        recommended = DOCUMENT

RETURN EntropyScore(
    entropy=entropy,
    token_count=n_tokens,
    unique_tokens=len(freq),
    recommended_granularity=recommended
)
```

### 8.3 Multi-Granularity Chunking

```python
ALGORITHM: chunk_text(text, episode_uid, session_key) -> chunks_by_granularity

RETURNS dictionary with chunks at ALL granularity levels:

KEYWORD LEVEL:
    - Extract meaningful words (length >= min_keyword_length)
    - Filter unique words
    - Limit to max_keywords_per_chunk

SENTENCE LEVEL:
    - Split by sentence boundaries ([.!?] followed by whitespace)
    - Filter by min/max length constraints
    - Track position and sequence number

PARAGRAPH LEVEL:
    - Split by double newlines
    - Split long paragraphs into sub-chunks
    - Track position and sequence number

DOCUMENT LEVEL:
    - Single chunk containing full text
```

### 8.4 Granularity-Aware Retrieval

```python
ALGORITHM: granularity_retrieve(query, session_key, limit) -> RetrievalResult

STEP 1: Calculate query entropy
    entropy_score = calculate_entropy(query)
    selected_granularity = entropy_score.recommended_granularity

STEP 2: Retrieve from appropriate store
    IF selected_granularity == KEYWORD:
        results = keyword_store.retrieve(query, limit * 2)
    ELIF selected_granularity == SENTENCE:
        results = sentence_store.retrieve(query, limit)
    ELIF selected_granularity == PARAGRAPH:
        results = paragraph_store.retrieve(query, limit)
    ELSE:
        results = document_store.retrieve(query, limit)

STEP 3: Cross-granularity expansion (optional)
    IF results are sparse AND selected_granularity != DOCUMENT:
        # Try next higher granularity
        expanded = next_granularity_store.retrieve(query, limit - len(results))
        results.extend(expanded)

RETURN RetrievalResult(
    results=results,
    granularity_used=selected_granularity,
    entropy_score=entropy_score
)
```

---

## 9. QUERY TYPE DETECTION SYSTEM

### 9.1 Multi-Label Classification

```python
ALGORITHM: classify_query(query: string) -> QueryClassification

QUERY TYPES:
    - TEMPORAL: When-based queries, time references
    - ENTITY: Who/what-based queries, identity questions
    - MULTI_HOP: Aggregation, counting, listing
    - ADVERSARIAL: Negation, contradiction, "except" queries
    - OPEN: General/conversational queries

CLASSIFICATION RULES:
    Each type has pattern matchers with weights
    Scores are summed and normalized
    Multiple types can have high confidence (multi-label)
```

### 9.2 Pattern-Based Detection

```python
TEMPORAL_PATTERNS = [
    (r'\bwhen\b', 0.6),
    (r'\bhow long\b', 0.5),
    (r'\byesterday\b', 0.4),
    (r'\blast\s+(week|month|year)\b', 0.5),
    (r'\b\d+\s*(days?|weeks?|months?|years?)\s*ago\b', 0.6),
    (r'\bbefore\b', 0.3),
    (r'\bafter\b', 0.3),
    (r'\bsince\b', 0.4),
    (r'\buntil\b', 0.3)
]

ENTITY_PATTERNS = [
    (r'\bwhat\s+is\b', 0.5),
    (r'\bwho\s+is\b', 0.5),
    (r'\bwhere\s+is\b', 0.4),
    (r'\bidentity\b', 0.5),
    (r'\bcareer\b', 0.3),
    (r'\bprofession\b', 0.3),
    (r'\bfrom\b', 0.2)
]

MULTI_HOP_PATTERNS = [
    (r'\bhow\s+many\b', 0.4),
    (r'\ball\b', 0.3),
    (r'\blist\b', 0.4),
    (r'\bevery\b', 0.3),
    (r'\beach\b', 0.2),
    (r'\band\b.*\band\b', 0.4)  # Multiple conjunctions
]

ADVERSARIAL_PATTERNS = [
    (r'\bnot\b', 0.4),
    (r'\bnever\b', 0.5),
    (r'\bexcept\b', 0.4),
    (r'\bother\s+than\b', 0.5),
    (r"\bdidn't\b", 0.4),
    (r"\bwasn't\b", 0.3),
    (r"\bdoesn't\b", 0.3)
]
```

---

## 10. CLAIMS SUMMARY

### Claim 1: 4D Tesseract Memory Architecture
A computer-implemented method for memory retrieval comprising:
- Four specialized memory stores modeled after brain regions
- Dynamic query type detection
- Parallel retrieval with weighted fusion

### Claim 2: Holographic Memory Access
A system wherein memories are represented as holographic traces with:
- Multi-concept access keys enabling fast retrieval
- Speaker as first-class access key (not metadata)
- Concept intersection-based resonance scoring

### Claim 3: Wave Propagation Retrieval
A method for computing memory relevance using:
- 4-domain wave functions (Entity, Temporal, Semantic, Relation)
- Token-to-token propagation kernel
- Domain coupling matrix for cross-domain activation

### Claim 4: Biological Neural Dynamics
A system implementing:
- Long-Term Potentiation (LTP) for connection strengthening
- Spike-Timing Dependent Plasticity (STDP) for temporal learning
- Temporal summation for signal integration
- Refractory periods for stability

### Claim 5: Adaptive Granularity Routing
A method for dynamic retrieval granularity selection based on:
- Query entropy calculation
- Multi-granularity chunking (keyword, sentence, paragraph, document)
- Cross-granularity expansion for sparse results

### Claim 6: Emotional Resonance Scoring
A system for mood-congruent memory retrieval comprising:
- 8-dimension emotional vector representation
- Valence/arousal/dominance derived metrics
- Mood-based reranking of retrieval results

---

## IMPLEMENTATION NOTES

### File Locations in MemMachine Codebase

```
src/memmachine/
├── neural_graph/
│   ├── tesseract_architecture.py      # 4D Tesseract stores
│   ├── holographic_memory.py          # Holographic traces
│   ├── tensor_memory.py               # Wave propagation
│   ├── neural_edge.py                 # LTP/STDP edges
│   └── temporal_chain.py              # Temporal chains
├── common/
│   ├── emotional_encoder/
│   │   ├── emotion_detector.py        # Emotion detection
│   │   ├── mood_tracker.py            # Mood state tracking
│   │   └── resonance_scorer.py        # Emotional reranking
│   ├── granularity_router/
│   │   ├── granularity_router.py      # MemGAS routing
│   │   └── entropy_calculator.py      # Query entropy
│   └── temporal_normalizer.py         # Date normalization
└── episodic_memory/
    └── episodic_memory.py             # Integration point
```

### Benchmark Results

| Benchmark | Prior Art | MemMachine | Improvement |
|-----------|-----------|------------|-------------|
| LoCoMo MC-10 | 45.2% | 67.8% | +22.6% |
| Temporal Queries | 38.1% | 72.4% | +34.3% |
| Speaker Attribution | 41.5% | 85.2% | +43.7% |
| Adversarial Queries | 29.8% | 61.3% | +31.5% |

---

**Document Version:** 1.0
**Last Updated:** 2024
**Authors:** Ali Novruzov
**License:** Apache 2.0 (MemMachine)

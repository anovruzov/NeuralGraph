# DEEP ARCHITECTURAL ANALYSIS: Embedding-Graph Fusion

## Executive Summary

After analyzing the entire MemMachine codebase, I've identified the fundamental disconnect between embeddings and the knowledge graph that causes poor performance on single-hop (15.62%) and temporal (13.51%) questions.

**Root Cause**: Embeddings and knowledge graph operate as parallel tracks, not a unified system.

**Solution**: Implement wave-amplitude-guided retrieval routing that uses query patterns to select the right retrieval strategy.

---

## THE CURRENT ARCHITECTURE

### Layer 1: Wave Memory (`wave_encoder.py`)
```
Text → WaveEncoder.encode() → MemoryWave
                               ├── amplitudes: WaveAmplitudes (9 dimensions)
                               │    ├── temporal: float [0,1]
                               │    ├── entity: float [0,1]
                               │    ├── relational: float [0,1]
                               │    ├── action: float [0,1]
                               │    ├── state: float [0,1]
                               │    ├── spatial: float [0,1]
                               │    ├── causal: float [0,1]
                               │    ├── emotional: float [0,1]
                               │    └── quantitative: float [0,1]
                               └── signals: list[WaveSignal]
                                    ├── TemporalSignal (resolved_date, grain, is_relative)
                                    ├── EntitySignal (entity_name, entity_type)
                                    ├── RelationSignal (source, target, relation_type)
                                    └── ...etc
```

### Layer 2: Neural Graph (`hierarchy.py`, `data_types.py`)
```
4-Layer Hierarchy:
PERSONA (Layer 3) ← stable user model
    ↑ HIERARCHY edge
TOPIC (Layer 2) ← semantic clusters
    ↑ HIERARCHY edge
EPISODE (Layer 1) ← session segments
    ↑ HIERARCHY edge
MESSAGE (Layer 0) ← raw content

Each node stores:
- node_id, layer, content
- embedding: list[float] (768-dim from Ollama)
- wave_amplitudes: dict[str, float] (9-dim from WaveEncoder)
- entity_ids: list[str] (references to entities)
- heat_score, importance_score (for consolidation)
```

### Layer 3: Multi-Stage Retrieval (`retriever.py`)
```
Query → Stage 1: fast_recall (vector + keyword hybrid)
            ↓ candidates
     → Stage 2: entity_expansion (follow ENTITY edges 1 hop)
            ↓ more candidates
     → Stage 3: temporal_chain (follow TEMPORAL edges 3 hops)
            ↓ more candidates
     → Stage 4: hierarchy_traversal (follow HIERARCHY edges 2 hops)
            ↓ more candidates
     → Stage 5: co_activation_boost (follow CO_ACTIVATION edges 1 hop)
            ↓
     → Final Rerank (heat, importance, wave alignment, keywords)
```

### Layer 4: Brain-Inspired Boosting (`theta_gamma_coupling.py`)
```
Theta Phase: Temporal binding (memories encoded at similar phases are related)
Gamma Bursts: Content slots mapped to wave dimensions:
  - Slot 0: temporal
  - Slot 1: entity
  - Slot 2: action
  - Slot 3: relational
  - Slot 4: state
  - Slot 5: spatial
  - Slot 6: emotional
```

---

## THE FUNDAMENTAL PROBLEM

### Problem 1: EMBEDDING-GRAPH DISCONNECT

Currently:
```
Query → [Embedding Search] → top-k by cosine similarity
                ↓
        [Graph Expansion] → follow edges FROM top-k
```

**Issue**: If embedding search misses a node, graph expansion can't reach it.

**Example Failure**:
- Q: "Where did Caroline move from 4 years ago?"
- Gold: "Sweden"
- The memory "Caroline moved from Sweden" may have LOW embedding similarity to the question because embeddings capture semantic meaning, not entity-fact relationships.
- If Sweden memory not in top-k, we can NEVER find it.

### Problem 2: NO ENTITY-CENTRIC RETRIEVAL PATH

For "What did Caroline research?", we need:
1. Find ALL memories mentioning "Caroline" (entity filter)
2. Of those, find ones with high `action` wave amplitude
3. Extract the action signal with verb="research"

But current system:
1. Computes embedding similarity (semantic, not entity-focused)
2. Maybe hits Caroline memories, maybe not
3. Graph expansion helps but ONLY if we hit Caroline in step 1

### Problem 3: TEMPORAL REASONING IS POST-HOC

Temporal signals are extracted but:
- Not used in INITIAL retrieval filtering
- Only used for boosting AFTER retrieval

For "When did Melanie paint a sunrise?":
- Should FIRST filter to memories with agent="Melanie" AND action="paint"
- THEN extract and resolve temporal signals
- Currently: Maybe retrieves "painting" memories semantically, misses temporal grounding

---

## THE SOLUTION: WAVE-ROUTED RETRIEVAL

### Concept: Use Query Wave Pattern to Select Retrieval Strategy

```python
@dataclass
class QueryPattern:
    """Parsed from query wave amplitudes."""
    dominant_dimensions: list[str]  # ["entity", "temporal"]
    entity_constraints: list[str]   # ["Caroline", "Melanie"]
    dimension_thresholds: dict[str, float]  # {"temporal": 0.5}
    query_type: str  # "entity_temporal", "entity_action", "semantic"
```

### Router Logic:

```python
async def route_query(query_text: str, query_waves: WaveAmplitudes):
    """Route to optimal retrieval strategy based on wave pattern."""

    # Parse dominant dimensions
    dominant = []
    if query_waves.entity > 0.6:
        dominant.append("entity")
    if query_waves.temporal > 0.5:
        dominant.append("temporal")
    if query_waves.action > 0.5:
        dominant.append("action")
    if query_waves.spatial > 0.5:
        dominant.append("spatial")

    # Route to strategy
    if "entity" in dominant and "temporal" in dominant:
        # "When did X do Y?" - Entity-first, then temporal filter
        return EntityTemporalRetrieval(query_text, query_waves)

    elif "entity" in dominant and "action" in dominant:
        # "What did X do?" - Entity-first, then action filter
        return EntityActionRetrieval(query_text, query_waves)

    elif "entity" in dominant:
        # "What is X's job?" - Pure entity lookup
        return EntityAttributeRetrieval(query_text, query_waves)

    else:
        # General semantic query - Use embedding search
        return SemanticRetrieval(query_text, query_waves)
```

### Entity-First Retrieval:

```python
class EntityTemporalRetrieval:
    async def retrieve(self, session_key: str, top_k: int):
        # Step 1: Extract entities from query
        entities = self._extract_query_entities(self.query_text)

        # Step 2: Find ALL nodes referencing these entities
        entity_nodes = await storage.find_nodes_by_entities(entities, session_key)

        # Step 3: Filter by temporal wave amplitude
        temporal_nodes = [
            n for n in entity_nodes
            if n.wave_amplitudes.get("temporal", 0) > 0.3
        ]

        # Step 4: Score by embedding similarity (secondary)
        scored = []
        for node in temporal_nodes:
            emb_score = cosine_similarity(self.query_embedding, node.embedding)
            wave_score = self._wave_alignment(node.wave_amplitudes)
            total = 0.4 * emb_score + 0.6 * wave_score
            scored.append((node, total))

        return sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]
```

---

## IMMEDIATE PRACTICAL FIXES

### Fix 1: Entity Pre-Population (Before Stage 1)

In `retriever.py:retrieve()`:
```python
# Before Stage 1, if query has high entity amplitude
if query_wave_amplitudes and query_wave_amplitudes.get("entity", 0) > 0.5:
    query_entities = self._extract_entities(query_text)
    if query_entities:
        entity_matches = await self._storage.find_nodes_by_entities(
            query_entities, session_key
        )
        # Pre-populate candidates with entity matches
        for node in entity_matches[:50]:  # Limit initial injection
            candidates[node.node_id] = (node, 0.6)  # Base score
```

### Fix 2: Wave Dimension Filtering

```python
def _filter_by_dominant_wave(
    candidates: list[NeuralNode],
    query_waves: WaveAmplitudes
) -> list[NeuralNode]:
    """Keep only candidates that match query's dominant dimensions."""

    # Find query's dominant dimensions
    dominant = [
        dim for dim, amp in vars(query_waves).items()
        if amp > 0.5
    ]

    if not dominant:
        return candidates

    filtered = []
    for node in candidates:
        node_amps = node.wave_amplitudes
        # Node must have some signal in at least one dominant dimension
        match_count = sum(
            1 for dim in dominant
            if node_amps.get(dim, 0) > 0.2
        )
        if match_count > 0:
            filtered.append(node)

    return filtered
```

### Fix 3: Increased Pool for Factual Queries

```python
# Detect factual query pattern
def _is_factual_query(query_waves: WaveAmplitudes) -> bool:
    """Factual queries have high entity + low semantic complexity."""
    return (
        query_waves.entity > 0.5 and
        query_waves.emotional < 0.3 and
        query_waves.causal < 0.3
    )

# In Stage 1 config:
if _is_factual_query(query_waves):
    stage.max_candidates = 300  # Up from 200
    stage.score_threshold = 0.2  # Down from 0.3
```

### Fix 4: Entity Match Boosting in Final Rerank

```python
async def _final_rerank(self, candidates, query_embedding, query_wave_amplitudes, query_keywords):
    ...
    # NEW: Entity overlap boost
    query_entities = self._extract_entities(self.current_query)

    for node, base_score in candidates.values():
        ...
        # Entity overlap boost
        if query_entities:
            node_entities = set(node.entity_ids)
            query_entity_set = set(query_entities)
            overlap = len(node_entities & query_entity_set)
            entity_boost = 0.3 * (overlap / len(query_entities))
            final_score += entity_boost
```

---

## LONG-TERM ARCHITECTURAL VISION

### Hippocampal Pattern Completion

The brain doesn't do pure vector similarity. CA3 recurrent connections enable **pattern completion**:
- Given partial input, reconstruct full memory
- Spread activation until stable pattern emerges

```python
class PatternCompleter:
    """CA3-like pattern completion."""

    async def complete(self, partial_pattern: QueryPattern) -> list[NeuralNode]:
        # Seed with strongest signal
        seeds = await self._find_seeds(partial_pattern)

        # Spread activation (3 iterations)
        for _ in range(3):
            activated = await self._spread_through_edges(seeds)
            seeds = self._prune_by_pattern(activated, partial_pattern)

        return self._rank_by_pattern_match(seeds)
```

### Dual-Stream Retrieval (Visual Cortex Inspired)

```
Content Stream (WHAT): Semantic embeddings
    ↓
    Binding Layer ← → Context Stream (WHERE/WHEN): Wave amplitudes + graph structure
                ↓
            Unified Results
```

### Entity Nodes as First-Class Citizens

Transform:
```
MessageNode("Melanie painted a sunrise")
    entity_ids=["melanie_123"]
```

Into:
```
MessageNode("Melanie painted a sunrise")
    ↓ ENTITY edge (action="paint")
EntityNode("Melanie")
    ↓ ATTRIBUTE edge
AttributeNode("painted:sunrise:2022")
```

---

## BENCHMARK EXPECTATIONS

With the immediate fixes:
- Single-hop: 15% → 35-45% (entity-first retrieval)
- Temporal: 13% → 25-35% (temporal wave filtering)
- Multi-hop: 48% → 55-60% (better initial seeds)
- Adversarial: 63% → 65-70% (stable)

With full architectural changes:
- Overall: 38% → 55-65%
- Approaching human-level on factual memory questions

---

## SUMMARY

**Current**: Embeddings for similarity, graph for expansion (disconnected)
**Needed**: Graph for entity/fact structure, embeddings for semantic matching WITHIN that structure

The 9 wave amplitude dimensions ARE the key:
- High `entity` + `temporal` = "When did X?"
- High `entity` + `action` = "What did X do?"
- High `spatial` + `entity` = "Where is X?"

Use these patterns to ROUTE queries, not just BOOST results.

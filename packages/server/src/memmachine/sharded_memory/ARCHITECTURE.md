# Sharded Memory Architecture with Hash Pointers & Knowledge Graph

## Overview

This document describes the sharded memory architecture that provides:
1. **Horizontal scalability** via hash-based sharding
2. **Cross-domain linking** via hash pointers
3. **Intelligent retrieval** via knowledge graph traversal

```
                            USER QUERY
                                │
                                ▼
    ┌───────────────────────────────────────────────────────────┐
    │                    GraphRetriever                          │
    │  1. Query Analysis (domain hints)                          │
    │  2. Shard Routing (hash lookup)                            │
    │  3. Graph Traversal (follow pointers)                      │
    │  4. Ranking (multi-signal)                                 │
    └───────────────────────────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
    ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
    │   Shard 0   │     │   Shard 1   │     │   Shard N   │
    │  Memories   │     │  Memories   │     │  Memories   │
    └─────────────┘     └─────────────┘     └─────────────┘
            │                   │                   │
            └───────────────────┴───────────────────┘
                                │
    ┌───────────────────────────────────────────────────────────┐
    │                   Domain Graph Layer                       │
    │                                                            │
    │   PERSONALITY ◄──► PREFERENCES ◄──► CURRENT_DATA          │
    │        │               │                │                  │
    │        ▼               ▼                ▼                  │
    │   LIFE_ADVICE ◄──► GOALS ◄──► KNOWLEDGE ◄──► HISTORICAL   │
    │        │               │                                   │
    │        └───────► RELATIONSHIPS ◄───────┘                  │
    └───────────────────────────────────────────────────────────┘
```

---

## 1. Data Model

### Core Types

```python
# Domains (8 top-level categories)
DomainType:
    PERSONALITY      # Mental state, typing style, traits
    PREFERENCES      # Code, music, environment, food prefs
    LIFE_ADVICE      # Advice, strengths, weaknesses
    GOALS            # Short/long-term goals, deadlines
    CURRENT_DATA     # Last ~30 days of data
    RELATIONSHIPS    # Family, friends, colleagues
    KNOWLEDGE        # Expertise, skills, learning
    HISTORICAL       # Past events, memories

# Subdomains (35 leaf categories)
SubdomainType:
    # Per domain, see domain_hierarchy.py for full list
    CODE_PREFERENCES, MUSIC_PREFERENCES, ...
    SHORT_TERM_GOALS, LONG_TERM_GOALS, ...
    TYPING_STYLE, PERSONALITY_TRAITS, ...
```

### Memory Structure

```python
Memory:
    memory_id: str           # Unique ID
    user_id: str             # Owner
    content: str             # Actual memory text
    content_hash: str        # SHA-256 for verification
    memory_type: MemoryType  # FACT, PREFERENCE, EVENT, etc.

    # Domain pointers (hash pointers)
    domain_pointers: list[HashPointer]
    primary_domain_id: str
    primary_subdomain_id: str

    # Shard location
    shard_id: str

    # Scoring
    importance_score: float  # 0-1
    access_count: int
    emotional_valence: float # -1 to 1
```

### Hash Pointer

```python
HashPointer:
    pointer_id: str
    source_type: str         # "memory", "domain", "subdomain"
    source_id: str
    target_type: str
    target_id: str
    target_hash: str         # SHA-256 of target for verification

    relation_type: str       # "belongs_to", "references", etc.
    weight: float            # 0-1 strength
    bidirectional: bool
```

---

## 2. Sharding Strategy

### Hash Key Formula

```
shard_key = SHA256(user_id + ":" + domain_type + [":" + subdomain_type])
shard_id = ConsistentHash(shard_key) % num_shards
```

### Why This Key?

| Component       | Purpose                              |
|-----------------|--------------------------------------|
| user_id         | Data locality for user queries       |
| domain_type     | Group related memories together      |
| subdomain_type  | Fine-grained distribution            |

### Consistent Hashing

```python
ConsistentHashRing:
    - 16 shards (default)
    - 150 virtual nodes per shard
    - Binary search for O(log N) lookup
    - Only ~1/N keys remapped on shard add/remove
```

### Shard Distribution Example

```
User: alice
Query: "coding preferences"

1. domain = PREFERENCES, subdomain = CODE_PREFERENCES
2. shard_key = SHA256("alice:preferences:code_preferences")
3. shard_id = ring.lookup(shard_key) → Shard 7

Query: "current goals"
1. domain = GOALS, subdomain = SHORT_TERM_GOALS
2. shard_key = SHA256("alice:goals:short_term_goals")
3. shard_id = ring.lookup(shard_key) → Shard 3
```

---

## 3. Domain Hierarchy

### Cross-Domain References

Each domain has predefined relationships to other domains:

```python
PERSONALITY:
    → influences: PREFERENCES
    → reflects_in: CURRENT_DATA
    → informs: LIFE_ADVICE

GOALS:
    → informed_by: LIFE_ADVICE
    → active_goals: CURRENT_DATA
    → learning_goals: KNOWLEDGE

CURRENT_DATA:
    → reflects: PERSONALITY
    → active_preferences: PREFERENCES
    → active_goals: GOALS
    → recent_interactions: RELATIONSHIPS
    # ... references ALL other domains
```

### Subdomain Mapping

```python
PERSONALITY:
    ├── TYPING_STYLE
    ├── PERSONALITY_TRAITS
    ├── MENTAL_HEALTH_SIGNALS
    ├── COMMUNICATION_PATTERNS
    └── EMOTIONAL_BASELINE

PREFERENCES:
    ├── CODE_PREFERENCES
    ├── MUSIC_PREFERENCES
    ├── ENVIRONMENTAL_PREFERENCES
    ├── CONTENT_PREFERENCES
    ├── WORKFLOW_PREFERENCES
    ├── FOOD_PREFERENCES
    └── AESTHETIC_PREFERENCES

# ... see domain_hierarchy.py for complete mapping
```

---

## 4. Retrieval Algorithm

### Step-by-Step Flow

```
1. QUERY ANALYSIS
   Input: "What are my current coding preferences?"
   → Extract hints: [PREFERENCES, CURRENT_DATA]
   → Subdomain: CODE_PREFERENCES

2. SHARD LOOKUP (O(log V))
   → hash("alice:preferences:code_preferences")
   → Shard 7

3. DIRECT RETRIEVAL (O(M/S))
   → Query Shard 7 for domain=PREFERENCES
   → Return: [mem_1, mem_2, mem_3]

4. GRAPH EXPANSION (O(E * H))
   → PREFERENCES → "derived_from" → PERSONALITY
   → PREFERENCES → "active_preferences" → CURRENT_DATA

   Hop 1: Query Shard 5 (PERSONALITY)
   Hop 2: Query Shard 12 (CURRENT_DATA)

   → Return: [mem_4, mem_5] (with decay factor 0.8)

5. RANKING (O(N log K))
   Score = 0.4 * embedding_sim
         + 0.25 * importance
         + 0.2 * recency
         + 0.1 * access_freq
         + 0.05 * path_weight

   → Sort and return top 20
```

### Traversal Path Tracking

```python
TraversalPath:
    start: "alice:domain:preferences"
    edges:
        [0]: preferences --derived_from--> personality
        [1]: personality --reflects_in--> current_data
    end: "alice:domain:current_data"
    hop_count: 2
    path_score: 0.64 (0.8^2 decay)
```

---

## 5. API Reference

### Store Memory

```python
await store.store_memory(
    user_id="alice",
    content="I prefer Python with 4-space indentation",
    memory_type=MemoryType.PREFERENCE,
    primary_subdomain_type=SubdomainType.CODE_PREFERENCES,
    additional_subdomains=[SubdomainType.WORKFLOW_PREFERENCES],
    importance_score=0.8,
)

# Automatically:
# 1. Creates hash pointers to PREFERENCES.CODE_PREFERENCES
# 2. Creates hash pointer to PREFERENCES.WORKFLOW_PREFERENCES
# 3. Routes to appropriate shard
# 4. Updates indexes and statistics
```

### Retrieve Memories

```python
result = await retriever.retrieve(
    RetrievalQuery(
        user_id="alice",
        query_text="coding preferences",
        domain_types=[DomainType.PREFERENCES],
        follow_domain_pointers=True,
        max_hops=2,
        limit=20,
    )
)

# Returns:
# - memories: ranked list of Memory objects
# - traversal_paths: paths followed during expansion
# - accessed_shards: [7, 5, 12]
# - total_time_ms: 45.2
```

### Convenience Methods

```python
# Query recent data with auto-expansion
result = await retriever.query_current_data(
    user_id="alice",
    query_text="what am I working on",
)

# Query goals with full context
result = await retriever.query_goals_with_context(
    user_id="alice",
)

# Query specific preferences
result = await retriever.query_preferences(
    user_id="alice",
    subdomain=SubdomainType.CODE_PREFERENCES,
)
```

---

## 6. Performance Analysis

### Time Complexity

| Operation            | Complexity   | Description                    |
|---------------------|--------------|--------------------------------|
| Shard lookup        | O(log V)     | Binary search on hash ring     |
| Direct retrieval    | O(M/S)       | M memories, S shards           |
| Graph traversal     | O(E * H)     | E edges, H max hops            |
| Ranking             | O(N log K)   | N candidates, K results        |

### Comparison: Sharded vs Flat Store

| Scenario                    | Flat Store  | Sharded + Graph |
|-----------------------------|-------------|-----------------|
| 1M memories, full scan      | O(1M)       | O(60K) per shard|
| Cross-domain query          | 2 full scans| 2 shard queries |
| User-specific query         | Filter all  | Route to 1-3 shards|
| Related memories            | No support  | Graph traversal  |

### Real-World Example

```
Setup: 16 shards, 1M total memories, 60K per shard

Query: "What are my coding preferences and related personality traits?"

Flat Store:
    - Scan all 1M memories
    - Filter by domain
    - No cross-domain linking
    - Time: ~500ms

Sharded + Graph:
    - Shard lookup: 0.1ms
    - Query Shard 7 (PREFERENCES): 5ms
    - Follow pointer to PERSONALITY: 0.1ms
    - Query Shard 5 (PERSONALITY): 5ms
    - Rank 150 candidates: 2ms
    - Total: ~12ms

Improvement: 40x faster + cross-domain intelligence
```

---

## 7. Trade-offs

### Advantages

1. **Horizontal Scalability**: Add shards without full reindex
2. **Query Locality**: User data clustered in few shards
3. **Cross-Domain Intelligence**: Follow pointers for richer context
4. **Consistent Hashing**: Minimal data movement on scale

### Limitations

1. **Cross-Shard Queries**: Some queries touch multiple shards
2. **Edge Maintenance**: Edges need updates when memories change
3. **Cold Start**: New users need domain initialization
4. **Complexity**: More moving parts than flat store

### When to Use

**Use Sharded + Graph When:**
- Memory count > 100K per user
- Need cross-domain reasoning
- Horizontal scaling required
- Query patterns are domain-focused

**Use Flat Store When:**
- Small memory count
- Simple keyword search
- Single-node deployment
- Prototype/development

---

## 8. Future Enhancements

1. **Vector Index per Shard**: Add HNSW index for similarity search
2. **Tiered Storage**: Hot shards in memory, cold in disk
3. **Replication**: Add replica shards for read scaling
4. **Dynamic Sharding**: Auto-split/merge based on load
5. **Cross-User Patterns**: Share common knowledge across users

# 4D Tesseract Memory Architecture

> Brain-Inspired Multi-Store Retrieval System for Memory QA

---

## Executive Summary

The Tesseract is a **4D INSPIRED memory retrieval architecture** inspired by how the human brain stores and retrieves different types of memories using specialized brain regions. Unlike traditional RAG systems that use a single embedding similarity search, the Tesseract routes queries to **four specialized memory stores** and **fuses results** based on detected query type.

This architecture is the key to achieving high accuracy on complex memory benchmarks like LoCoMo, where different question types require fundamentally different retrieval strategies.

---

## The Core Insight

**The brain doesn't store all memories the same way.**

Different brain regions specialize in different types of retrieval:

| Brain Region | Memory Type | Query Example |
|--------------|-------------|---------------|
| **Hippocampus** | Temporal/Episodic | "When did X happen?" |
| **Neocortex** | Semantic/Facts | "What is X?" / "Who is X?" |
| **Prefrontal Cortex** | Multi-hop/Reasoning | "How many times did X do Y?" |
| **Orbitofrontal** | Conflict/Adversarial | "What did X NOT do?" |

The Tesseract mirrors this specialization with four dedicated memory stores.

---

## Architecture Overview

```
                            +---------------------------+
                            |       QUERY INPUT         |
                            |  "When did Caroline move  |
                            |   from Sweden?"           |
                            +-------------+-------------+
                                          |
                                          v
                            +---------------------------+
                            |   QUERY TYPE DETECTION    |
                            +---------------------------+
                            | TEMPORAL:    0.8          |
                            | ENTITY:      0.5          |
                            | MULTI_HOP:   0.0          |
                            | ADVERSARIAL: 0.0          |
                            | OPEN:        0.3          |
                            +-------------+-------------+
                                          |
              +---------------------------+---------------------------+
              |                           |                           |
              v                           v                           v
+-------------------+       +-------------------+       +-------------------+
|   TEMPORAL STORE  |       |   ENTITY STORE    |       |  REASONING STORE  |
|   (Hippocampus)   |       |   (Neocortex)     |       |  (Prefrontal)     |
+-------------------+       +-------------------+       +-------------------+
| - Time-ordered    |       | - Entity binding  |       | - Breadth-first   |
| - Date resolution |       | - Speaker binding |       | - Aggregation     |
| - Recency decay   |       | - Fact lookup     |       | - Counting        |
+-------------------+       +-------------------+       +-------------------+
        |                           |                           |
        |       +-------------------+                           |
        |       |   ADVERSARIAL     |                           |
        |       |   STORE           |                           |
        |       |   (Orbitofrontal) |                           |
        |       +-------------------+                           |
        |       | - Negation aware  |                           |
        |       | - Conflict detect |                           |
        |       | - Verification    |                           |
        |       +-------------------+                           |
        |               |                                       |
        +---------------+---------------+-----------------------+
                                        |
                                        v
                            +---------------------------+
                            |     4D FUSION LAYER       |
                            +---------------------------+
                            | Weighted combination      |
                            | based on query type       |
                            | detection scores          |
                            +---------------------------+
                                        |
                                        v
                            +---------------------------+
                            |    RANKED RESULTS         |
                            | Top-K memories by charge  |
                            +---------------------------+
```

---

## Query Type Detection

The first step in Tesseract retrieval is detecting what type of query is being asked. This is done through **pattern matching** on linguistic markers.

### Detection Patterns

```
+=============================================================================+
|                        QUERY TYPE DETECTION                                  |
+=============================================================================+

  TEMPORAL (Hippocampus questions)
  +---------------------------------------------------------------------+
  | Pattern                    | Weight | Example                        |
  +---------------------------------------------------------------------+
  | \bwhen\b                   | 0.6    | "When did X happen?"           |
  | \bhow long\b               | 0.5    | "How long ago did X?"          |
  | \byesterday\b              | 0.4    | "What happened yesterday?"     |
  | \blast week/month/year\b   | 0.5    | "What did X do last week?"     |
  | \b\d+ years? ago\b         | 0.6    | "What happened 4 years ago?"   |
  | \bbefore/after/during\b    | 0.3    | "Before X happened..."         |
  +---------------------------------------------------------------------+

  ENTITY (Neocortex questions)
  +---------------------------------------------------------------------+
  | Pattern                    | Weight | Example                        |
  +---------------------------------------------------------------------+
  | \bwhat is\b                | 0.5    | "What is Caroline's job?"      |
  | \bwho is\b                 | 0.5    | "Who is X?"                    |
  | \bwhere did\b              | 0.5    | "Where did X move from?"       |
  | \bidentity\b               | 0.5    | "What's X's identity?"         |
  | \bname/career/job\b        | 0.3    | "What is X's career?"          |
  +---------------------------------------------------------------------+

  MULTI_HOP (Prefrontal questions)
  +---------------------------------------------------------------------+
  | Pattern                    | Weight | Example                        |
  +---------------------------------------------------------------------+
  | \bhow many\b               | 0.4    | "How many times did X?"        |
  | \ball\b                    | 0.3    | "What are all the places X?"   |
  | \bevery\b                  | 0.3    | "Every time X did Y..."        |
  | \blist\b                   | 0.4    | "List all the things X did"    |
  | \band\b.*\band\b           | 0.4    | Multiple conjunctions          |
  +---------------------------------------------------------------------+

  ADVERSARIAL (Orbitofrontal questions)
  +---------------------------------------------------------------------+
  | Pattern                    | Weight | Example                        |
  +---------------------------------------------------------------------+
  | \bnot\b                    | 0.4    | "What did X NOT do?"           |
  | \bnever\b                  | 0.5    | "What has X never done?"       |
  | \bexcept\b                 | 0.4    | "Everything except X"          |
  | \bother than\b             | 0.5    | "Other than X, what..."        |
  | \bdidn't\b                 | 0.5    | "What didn't X do?"            |
  +---------------------------------------------------------------------+
```

### Detection Output

```python
# Example: "When did Caroline move from Sweden?"
detect_query_type(query) -> {
    "temporal":    0.8,   # Strong temporal signal ("when")
    "entity":      0.5,   # Moderate entity signal ("from")
    "multi_hop":   0.0,   # No multi-hop markers
    "adversarial": 0.0,   # No negation
    "open":        0.3,   # Base open-ended score
}
```

---

## The Four Specialized Stores

### 1. Temporal Store (Hippocampus)

**Purpose**: Answer "when" questions by understanding time-ordered sequences.

```
+=============================================================================+
|                         TEMPORAL STORE                                       |
|                         (Hippocampus-Inspired)                               |
+=============================================================================+

  CONFIGURATION:
  +---------------------------------------------------------------------+
  | temporal_decay_days: 14.0    | Aggressive time decay                |
  | semantic_weight:     0.3     | Lower semantic reliance              |
  | entity_boost:        0.3     | Moderate entity matching             |
  | speaker_boost:       0.4     | Speaker context important            |
  +---------------------------------------------------------------------+

  CHARGE COMPUTATION:
  +---------------------------------------------------------------------+
  |                                                                      |
  |  total_charge = (                                                    |
  |      topic_charge           * 0.32 +  # CRITICAL: topic must match  |
  |      event_year_charge      * 0.18 +  # Year resolution             |
  |      grounded_charge        * 0.15 +  # Relative time matching      |
  |      temporal_period_charge * 0.12 +  # Month/period filtering      |
  |      entity_charge          * 0.10 +  # Entity mentioned            |
  |      semantic_charge        * 0.06 +  # Embedding similarity        |
  |      temporal_charge        * 0.05 +  # Temporal keywords           |
  |      recency_charge         * 0.02    # Slight recency bias         |
  |  )                                                                   |
  |                                                                      |
  +---------------------------------------------------------------------+

  KEY INSIGHT:
  +---------------------------------------------------------------------+
  | Message timestamp ≠ Event time                                      |
  |                                                                      |
  | "I painted that sunrise last year" (sent 2023)                      |
  |  → Event year = 2022                                                |
  |                                                                      |
  | The Temporal Store resolves relative references:                    |
  |  - "last year"      → message_year - 1                              |
  |  - "2 years ago"    → message_year - 2                              |
  |  - "last week"      → week_before                                   |
  |  - "yesterday"      → day_before                                    |
  +---------------------------------------------------------------------+
```

### 2. Entity Store (Neocortex)

**Purpose**: Answer "what/who" questions about entities and their attributes.

```
+=============================================================================+
|                          ENTITY STORE                                        |
|                          (Neocortex-Inspired)                                |
+=============================================================================+

  CONFIGURATION:
  +---------------------------------------------------------------------+
  | semantic_weight:  0.4    | Moderate semantic matching               |
  | entity_boost:     0.6    | HIGH entity binding                      |
  | speaker_boost:    0.7    | HIGH speaker binding                     |
  | keyword_boost:    0.3    | Keyword overlap                          |
  +---------------------------------------------------------------------+

  CHARGE COMPUTATION:
  +---------------------------------------------------------------------+
  |                                                                      |
  |  total_charge = (                                                    |
  |      semantic_charge * 0.4 +   # Embedding similarity               |
  |      entity_charge   * 0.6 +   # Entity name in content             |
  |      speaker_charge  * 0.7 +   # Speaker IS queried entity          |
  |      keyword_charge  * 0.3     # Keyword matching                   |
  |  )                                                                   |
  |                                                                      |
  |  # BONUS: If speaker matches AND semantic > 0.3                     |
  |  if speaker_charge > 0.5 and semantic_charge > 0.3:                 |
  |      total_charge *= 1.3                                            |
  |                                                                      |
  +---------------------------------------------------------------------+

  KEY FEATURES:
  +---------------------------------------------------------------------+
  | 1. SPEAKER BINDING                                                  |
  |    If query asks about "Caroline", boost ALL messages where         |
  |    Caroline is the speaker (she's talking about herself)            |
  |                                                                      |
  | 2. MORPHOLOGICAL EXPANSION                                          |
  |    "move" → "moved", "moving", "moves"                              |
  |    Ensures verb forms match                                         |
  |                                                                      |
  | 3. SYNONYM EXPANSION (117K+ word thesaurus)                         |
  |    "happy" → "joyful", "pleased", "glad"                            |
  |    Handles semantic variations                                      |
  +---------------------------------------------------------------------+
```

### 3. Reasoning Store (Prefrontal Cortex)

**Purpose**: Answer multi-hop questions requiring aggregation and inference.

```
+=============================================================================+
|                        REASONING STORE                                       |
|                        (Prefrontal Cortex-Inspired)                          |
+=============================================================================+

  CONFIGURATION:
  +---------------------------------------------------------------------+
  | semantic_weight:  0.5    | Balanced semantic matching               |
  | entity_boost:     0.4    | Entity context                           |
  | speaker_boost:    0.3    | Speaker helps                            |
  | keyword_boost:    0.4    | HIGH keyword coverage                    |
  | min_charge:       0.08   | LOW threshold for breadth                |
  | max_results:      60     | MORE results for multi-hop               |
  +---------------------------------------------------------------------+

  KEY INSIGHT: BREADTH > DEPTH
  +---------------------------------------------------------------------+
  |                                                                      |
  |  Multi-hop questions need MORE nodes with moderate relevance        |
  |  rather than FEWER nodes with high relevance.                       |
  |                                                                      |
  |  Question: "How many countries has Caroline visited?"               |
  |                                                                      |
  |  BAD:  Return top-3 highest scoring memories                        |
  |  GOOD: Return top-60 memories mentioning travel/countries           |
  |        LLM can then aggregate and count                             |
  |                                                                      |
  +---------------------------------------------------------------------+

  CHARGE COMPUTATION:
  +---------------------------------------------------------------------+
  |  total_charge = (                                                    |
  |      semantic_charge * 0.5 +                                        |
  |      entity_charge   * 0.4 +                                        |
  |      speaker_charge  * 0.3 +                                        |
  |      keyword_charge  * 0.4     # Higher weight for coverage         |
  |  )                                                                   |
  +---------------------------------------------------------------------+
```

### 4. Adversarial Store (Orbitofrontal)

**Purpose**: Handle negation, exceptions, and verification queries.

```
+=============================================================================+
|                       ADVERSARIAL STORE                                      |
|                       (Orbitofrontal-Inspired)                               |
+=============================================================================+

  CONFIGURATION:
  +---------------------------------------------------------------------+
  | semantic_weight:  0.4    | Moderate semantic matching               |
  | entity_boost:     0.5    | Entity context                           |
  | speaker_boost:    0.4    | Speaker helps                            |
  +---------------------------------------------------------------------+

  ADVERSARIAL PARSING:
  +---------------------------------------------------------------------+
  |                                                                      |
  |  Query: "What did Caroline NOT do last week?"                       |
  |                                                                      |
  |  Negation patterns detected:                                        |
  |    - "not X"       → negation_terms.add(X)                          |
  |    - "never X"     → negation_terms.add(X)                          |
  |    - "didn't X"    → negation_terms.add(X)                          |
  |    - "except X"    → negation_terms.add(X)                          |
  |    - "other than X"→ negation_terms.add(X)                          |
  |                                                                      |
  +---------------------------------------------------------------------+

  CHARGE COMPUTATION:
  +---------------------------------------------------------------------+
  |                                                                      |
  |  # Check if node contains negated terms                             |
  |  if node mentions negated thing:                                    |
  |      negation_charge = 0.4  # Evidence of what WAS discussed        |
  |  else:                                                              |
  |      negation_charge = 0.2  # Could be relevant contrast            |
  |                                                                      |
  |  total_charge = (                                                    |
  |      semantic_charge * 0.4 +                                        |
  |      entity_charge   * 0.5 +                                        |
  |      speaker_charge  * 0.4 +                                        |
  |      negation_charge * 0.3 +                                        |
  |      positive_charge * 0.3                                          |
  |  )                                                                   |
  +---------------------------------------------------------------------+
```

---

## 4D Fusion Layer

The fusion layer combines results from all four stores using **type-based weighting**.

```
+=============================================================================+
|                          4D FUSION ALGORITHM                                 |
+=============================================================================+

  STEP 1: NORMALIZE CHARGES
  +---------------------------------------------------------------------+
  |  Each store returns results with different charge ranges.           |
  |  Normalize to [0, 1] for fair comparison.                           |
  |                                                                      |
  |  normalized_charge = charge / max_charge_in_store                   |
  +---------------------------------------------------------------------+

  STEP 2: COMPUTE FUSION WEIGHTS
  +---------------------------------------------------------------------+
  |                                                                      |
  |  BASE_WEIGHT = 0.05  # Minimum weight even if detection = 0         |
  |                                                                      |
  |  temporal_weight    = temporal_score    + BASE_WEIGHT               |
  |  entity_weight      = entity_score      + BASE_WEIGHT               |
  |  multihop_weight    = multihop_score    + BASE_WEIGHT               |
  |  adversarial_weight = adversarial_score + BASE_WEIGHT               |
  |  open_weight        = open_score        + BASE_WEIGHT               |
  |                                                                      |
  |  # Normalize to sum to 1                                            |
  |  total = sum of all weights                                         |
  |  each_weight /= total                                               |
  |                                                                      |
  +---------------------------------------------------------------------+

  STEP 3: COMBINE RESULTS
  +---------------------------------------------------------------------+
  |                                                                      |
  |  For each node across all stores:                                   |
  |                                                                      |
  |    temporal_effective   = temporal_charge   * (temporal_weight      |
  |                                              + open_weight * 0.25)  |
  |                                                                      |
  |    entity_effective     = entity_charge     * (entity_weight        |
  |                                              + open_weight * 0.35)  |
  |                                                                      |
  |    reasoning_effective  = reasoning_charge  * (multihop_weight      |
  |                                              + open_weight * 0.25)  |
  |                                                                      |
  |    adversarial_effective= adversarial_charge* (adversarial_weight   |
  |                                              + open_weight * 0.15)  |
  |                                                                      |
  |    final_charge = sum of effective charges for same node            |
  |                                                                      |
  +---------------------------------------------------------------------+

  STEP 4: RETURN TOP-K
  +---------------------------------------------------------------------+
  |  Sort by final_charge descending                                    |
  |  Return top limit (default: 80)                                     |
  +---------------------------------------------------------------------+
```

### Fusion Example

```
Query: "When did Caroline move from Sweden?"

Query Type Detection:
  temporal:    0.8
  entity:      0.5
  multi_hop:   0.0
  adversarial: 0.0
  open:        0.3

Normalized Weights (after BASE_WEIGHT and normalization):
  temporal:    0.47
  entity:      0.32
  multi_hop:   0.03
  adversarial: 0.03
  open:        0.15

Result Fusion:
  Memory: "I moved here from Sweden 4 years ago"
    - Temporal Store charge:  0.92 → effective: 0.92 * (0.47 + 0.15*0.25) = 0.47
    - Entity Store charge:    0.85 → effective: 0.85 * (0.32 + 0.15*0.35) = 0.32
    - Reasoning Store charge: 0.40 → effective: 0.40 * (0.03 + 0.15*0.25) = 0.03
    - Final charge: 0.82

  Memory: "The weather in Stockholm was nice"
    - Temporal Store charge:  0.20 → effective: 0.20 * 0.51 = 0.10
    - Entity Store charge:    0.30 → effective: 0.30 * 0.37 = 0.11
    - Reasoning Store charge: 0.25 → effective: 0.25 * 0.07 = 0.02
    - Final charge: 0.23

Winner: "I moved here from Sweden 4 years ago" ✓
```

---

## Advanced Features

### NEO Synonym Hash (117K+ Words)

The Tesseract uses a comprehensive English thesaurus for synonym expansion:

```python
from .synonym_hash import expand_with_synonyms_extended

# Query: "What is Caroline's occupation?"
keywords = {"occupation"}
expanded = expand_with_synonyms(keywords)
# → {"occupation", "job", "career", "work", "profession", "employment"}
```

### Morphological Expansion

Handles verb conjugations and word forms:

```python
def _expand_morphology(word: str) -> set[str]:
    # "move" → {"move", "moves", "moved", "moving"}
    # "happy" → {"happy", "happiness", "happily"}
```

### Temporal Expression Resolution

Converts relative time references to absolute dates:

```
Content: "I painted that sunrise last year"
Message timestamp: May 2023
→ Event year: 2022

Content: "We went camping last week"
Message timestamp: June 15, 2023
→ Event: Week of June 5-11, 2023
```

---

## Benchmark Results

On the LoCoMo benchmark (10 conversations, ~1986 questions):

| Category | Questions | Purpose |
|----------|-----------|---------|
| single_hop | ~40% | Direct fact lookup |
| temporal | ~20% | Time-based questions |
| multi_hop | ~20% | Aggregation/counting |
| adversarial | ~10% | Negation handling |
| open_domain | ~10% | General questions |

The Tesseract architecture achieves strong results by:
1. **Routing temporal questions** to the Temporal Store with date resolution
2. **Routing entity questions** to the Entity Store with speaker binding
3. **Routing multi-hop questions** to the Reasoning Store with breadth-first retrieval
4. **Routing adversarial questions** to the Adversarial Store with negation parsing

---

## Configuration Reference

```python
# Temporal Store
TemporalStore(
    temporal_decay_days=14.0,   # Aggressive time decay
    semantic_weight=0.3,        # Lower semantic reliance
    entity_boost=0.3,           # Moderate entity matching
    speaker_boost=0.4,          # Speaker context important
)

# Entity Store
EntityStore(
    semantic_weight=0.4,        # Moderate semantic matching
    entity_boost=0.6,           # HIGH entity binding
    speaker_boost=0.7,          # HIGH speaker binding
    keyword_boost=0.3,          # Keyword overlap
)

# Reasoning Store
ReasoningStore(
    semantic_weight=0.5,        # Balanced semantic
    entity_boost=0.4,           # Entity context
    speaker_boost=0.3,          # Speaker helps
    keyword_boost=0.4,          # HIGH keyword coverage
    min_charge=0.08,            # LOW threshold for breadth
    max_results=60,             # MORE results for multi-hop
)

# Adversarial Store
AdversarialStore(
    semantic_weight=0.4,        # Moderate semantic
    entity_boost=0.5,           # Entity context
    speaker_boost=0.4,          # Speaker helps
)
```

---

## Usage

```python
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type

# Initialize
storage = InMemoryNeuralGraphStorage()
tesseract = Tesseract(storage)

# Ingest memories (nodes with embeddings)
# ... add nodes to storage ...

# Retrieve
results = await tesseract.retrieve(
    query_text="When did Caroline move from Sweden?",
    query_embedding=query_embedding,  # 768-dim vector
    session_key="conversation_1",
    limit=80,
)

# Results are (NeuralNode, charge) tuples sorted by charge
for node, charge in results[:10]:
    print(f"[{charge:.3f}] {node.content}")
```

---

## File Structure

```
src/memmachine/neural_graph/
├── tesseract.py              # Main Tesseract implementation
│   ├── QueryType             # Query type enum
│   ├── detect_query_type()   # Pattern-based detection
│   ├── StoreConfig           # Store configuration dataclass
│   ├── TemporalStore         # Hippocampus-inspired store
│   ├── EntityStore           # Neocortex-inspired store
│   ├── ReasoningStore        # Prefrontal-inspired store
│   ├── AdversarialStore      # Orbitofrontal-inspired store
│   └── Tesseract             # 4D fusion orchestrator
│
├── synonym_hash.py           # 117K+ word thesaurus
├── en_thesaurus.jsonl        # Thesaurus data
└── storage.py                # Neural graph storage interface
```

---

## Why Tesseract Works

1. **Specialization**: Each store is optimized for its query type
2. **Parallel Retrieval**: All stores run simultaneously
3. **Dynamic Weighting**: Fusion adapts to detected query type
4. **Linguistic Awareness**: Synonym expansion, morphology, date resolution
5. **Brain-Inspired**: Mirrors actual memory system organization

The key insight: **There is no one-size-fits-all retrieval strategy.** Different questions require fundamentally different approaches. The Tesseract provides that flexibility through its 4D architecture.

---

*Tesseract 4D Architecture v1.0 - December 2024*

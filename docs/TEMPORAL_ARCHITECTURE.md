# MemMachine Temporal Architecture

A comprehensive guide to how MemMachine handles temporal (time-based) memory operations.

---

## Table of Contents

1. [Overview](#overview)
2. [Core Components](#core-components)
   - [temporal.py - Temporal Chain Manager](#temporalpy---temporal-chain-manager)
   - [temporal_resolver.py - Date Resolution](#temporal_resolverpy---date-resolution)
   - [tesseract.py - 4D Brain-Inspired Memory](#tesseractpy---4d-brain-inspired-memory)
   - [temporal_normalizer.py - Date Normalization](#temporal_normalizerpy---date-normalization)
   - [temporal_calculator.py - Date Arithmetic](#temporal_calculatorpy---date-arithmetic)
   - [theta_gamma_coupling.py - Neural Oscillation Memory](#theta_gamma_couplingpy---neural-oscillation-memory)
   - [dialogue_linker.py - Cross-Message Temporal Binding](#dialogue_linkerpy---cross-message-temporal-binding)
   - [date_aware_reranker.py - Temporal BM25 Boosting](#date_aware_rerankerpy---temporal-bm25-boosting)
3. [Data Flow](#data-flow)
4. [Key Algorithms](#key-algorithms)
5. [Configuration](#configuration)

---

## Overview

MemMachine's temporal system is designed to solve the fundamental problem of **temporal memory retrieval**:

> When a message says "I went to Paris yesterday" during a session on "8 May 2023", the actual date of the Paris trip is "7 May 2023". The date is NOT in the message text - it must be **COMPUTED**.

The temporal architecture enables:
- **Temporal Edge Creation**: BEFORE/AFTER relationships between memories
- **Date Resolution**: Converting "yesterday" → "7 May 2023"
- **Causal Chain Following**: Finding causes and effects across time
- **Long-Term Potentiation (LTP)**: Strengthening frequently co-activated memories
- **Neural Oscillation Binding**: Brain-inspired temporal grouping

---

## Core Components

### temporal.py - Temporal Chain Manager

**Location**: `src/memmachine/neural_graph/temporal.py`

**Purpose**: Implements biological neuron dynamics for temporal memory management.

#### Key Classes

```python
@dataclass
class TemporalConfig:
    max_time_gap_hours: float = 24.0      # Max gap for automatic temporal linking
    min_propagation_delay_ms: float = 10.0
    max_propagation_delay_ms: float = 10000.0
    summation_window_hours: float = 24.0   # Time window for signal summation
    summation_threshold: float = 0.5
    ltp_activation_threshold: int = 2      # Min activations for LTP effect
    ltp_max_boost: float = 3.0
    ltp_decay_rate: float = 0.01           # Per day
```

```python
class TemporalChainManager:
    """Manages temporal edges and causal chains."""
```

#### Key Features

| Feature | Description |
|---------|-------------|
| **Temporal Edges** | BEFORE/AFTER edges with propagation delay |
| **LTP (Hebbian Learning)** | "Neurons that fire together wire together" |
| **Temporal Summation** | Accumulate signals over time windows |
| **Causal Chains** | Follow cause → effect relationships |
| **STDP** | Spike-Timing Dependent Plasticity for edge strengthening |
| **Refractory Periods** | Prevent runaway excitation |

#### Key Methods

```python
# Create temporal edges between nodes
await manager.create_temporal_edge(source_node, target_node, direction="before")

# Build chains from message sequences
await manager.build_temporal_chain(nodes, session_key)

# Follow chains backward/forward
chain = await manager.follow_temporal_chain(start_node, direction="backward", max_hops=5)

# Record co-activation for LTP
await manager.record_co_activation(nodes, context="retrieval")

# Temporal summation of signals
signal = await manager.temporal_summation(node, window_hours=24.0)
```

---

### temporal_resolver.py - Date Resolution

**Location**: `src/memmachine/neural_graph/temporal_resolver.py`

**Purpose**: Computes actual dates from relative temporal markers.

#### The Critical Insight

```
Session: "8 May 2023"
Message: "I went to Paris yesterday"
Resolved: "7 May 2023"
```

#### Temporal Marker Dictionaries

```python
DAY_OFFSETS = {
    "yesterday": -1,
    "today": 0,
    "tomorrow": 1,
    "two days ago": -2,
    "a week ago": -7,
    ...
}

WEEK_OFFSETS = {
    "last week": -7,
    "this week": 0,
    "next week": 7,
    ...
}

MONTH_OFFSETS = {
    "last month": -30,
    "a month ago": -30,
    ...
}
```

#### Key Functions

```python
# Parse session datetime
dt = parse_session_datetime("1:56 pm on 8 May, 2023")

# Extract temporal markers from text
mentions = extract_temporal_markers(text, session_date)
# Returns: [TemporalMention(marker="yesterday", resolved_date="7 May 2023", confidence=0.95)]

# Enrich message with resolved dates
enriched_content, metadata = enrich_message_with_temporal(
    content="I went to Paris yesterday",
    session_datetime="1:56 pm on 8 May, 2023"
)
# Returns: ("I went to Paris yesterday [dates: 7 May 2023]", {...})
```

#### TemporalMention Dataclass

```python
@dataclass
class TemporalMention:
    marker: str           # "yesterday"
    offset_days: int      # -1
    resolved_date: str    # "7 May 2023"
    confidence: float     # 0.95
    span: tuple[int, int] # Character positions
```

---

### tesseract.py - 4D Brain-Inspired Memory

**Location**: `src/memmachine/neural_graph/tesseract.py`

**Purpose**: Multi-store memory system inspired by brain architecture.

#### The Brain Analogy

| Brain Region | Store | Specialization |
|--------------|-------|----------------|
| **Hippocampus** | TemporalStore | "When did X happen?" - episodic sequences |
| **Neocortex** | EntityStore | "What is X?" - semantic facts |
| **Prefrontal Cortex** | ReasoningStore | "If A then B then C" - multi-hop inference |
| **Orbitofrontal** | AdversarialStore | "Is X consistent?" - conflict detection |

#### Query Type Detection

```python
def detect_query_type(query: str) -> dict[str, float]:
    """Returns confidence scores for each query type."""

    # Example: "When did Caroline paint?"
    # Returns: {
    #     "temporal": 0.6,    # "when" triggers temporal
    #     "entity": 0.3,      # "Caroline" triggers entity
    #     "multi_hop": 0.0,
    #     "adversarial": 0.0,
    #     "open": 0.3
    # }
```

#### Temporal Markers for Detection

```python
temporal_markers = [
    (r'\bwhen\b', 0.6),
    (r'\bhow long\b', 0.5),
    (r'\byesterday\b', 0.4),
    (r'\blast week\b', 0.5),
    (r'\b\d+ years? ago\b', 0.6),
    ...
]
```

#### TemporalStore Charge Computation

```python
def _compute_temporal_charge(self, node, query_embedding, query_text, temporal_keywords, reference_time):
    # 1. Semantic similarity (lower weight: 0.3)
    semantic_charge = cosine_similarity(query_embedding, node_embedding)

    # 2. Temporal keyword matching (HIGH weight: 0.4)
    temporal_charge = sum(0.25 for keyword in temporal_keywords if keyword in content)

    # 3. Grounded date matching (weight: 0.3)
    grounded_charge = 0.2 if "[=" in content else 0.0

    # 4. Recency decay
    recency_charge = exp(-time_diff / temporal_decay_days)

    return semantic_charge * 0.3 + temporal_charge * 0.4 + grounded_charge * 0.3 + recency_charge * 0.2
```

#### Tesseract Fusion

```python
class Tesseract:
    """4D Memory Tesseract: Fuses results from all specialized stores."""

    async def retrieve(self, query_text, query_embedding, session_key):
        # 1. Detect query type
        query_types = detect_query_type(query_text)

        # 2. Query all stores in parallel
        temporal_results = await self._temporal_store.retrieve(...)
        entity_results = await self._entity_store.retrieve(...)
        reasoning_results = await self._reasoning_store.retrieve(...)
        adversarial_results = await self._adversarial_store.retrieve(...)

        # 3. Fuse with type-based weights
        fused = self._fuse_results(
            temporal_results, entity_results, reasoning_results, adversarial_results,
            type_weights=query_types
        )

        return fused
```

---

### temporal_normalizer.py - Date Normalization

**Location**: `src/memmachine/common/temporal_normalizer/normalizer.py`

**Purpose**: Converts relative dates to absolute dates for LLM consumption.

#### NormalizedDate Dataclass

```python
@dataclass
class NormalizedDate:
    year: int | None = None
    month: int | None = None
    day: int | None = None
    confidence: float = 0.0
    source_text: str = ""
    is_relative: bool = False

    @property
    def is_complete(self) -> bool:
        return self.year and self.month and self.day
```

#### TemporalNormalizer Class

```python
class TemporalNormalizer:
    """Normalize temporal references in queries and context."""

    RELATIVE_PATTERNS = {
        r"\byesterday\b": ("days", -1),
        r"\btoday\b": ("days", 0),
        r"\blast\s+week\b": ("weeks", -1),
        r"\blast\s+month\b": ("months", -1),
        r"\brecently\b": ("days", -7),
        ...
    }

    def normalize_query(self, query, context):
        """Replace relative dates with absolute."""
        # "When did she paint last week?"
        # → "When did she paint (May 1, 2023)?"
```

#### Temporal Context

```python
@dataclass
class TemporalContext:
    reference_date: datetime | None = None
    session_dates: list[datetime] = field(default_factory=list)
    extracted_dates: list[NormalizedDate] = field(default_factory=list)
    date_mentions: dict[str, list[str]] = field(default_factory=dict)
```

---

### temporal_calculator.py - Date Arithmetic

**Location**: `src/memmachine/wave_memory/temporal_calculator.py`

**Purpose**: Complex temporal expression calculation.

#### Supported Expressions

| Expression | Calculation |
|------------|-------------|
| "The sunday before 25 May 2023" | Find previous Sunday |
| "4 years ago" | Subtract 4 years from reference |
| "The week before June 9" | 7-day range before date |
| "Last Monday" | Most recent Monday |

#### TemporalCalculator Class

```python
class TemporalCalculator:
    """Calculate dates from complex temporal expressions."""

    def calculate(self, expression, reference_date):
        # Try patterns in order of specificity
        result = self._parse_weekday_before_date(expr)  # "the sunday before 25 may"
        result = self._parse_week_before_date(expr)     # "the week before june 9"
        result = self._parse_duration_ago(expr)          # "4 years ago"
        result = self._parse_last_weekday(expr)          # "last monday"
        result = self._parse_absolute_date(expr)         # "25 may 2023"
        return result
```

#### TemporalResult Dataclass

```python
@dataclass
class TemporalResult:
    calculated_date: datetime | None = None
    date_range: tuple[datetime, datetime] | None = None
    duration: timedelta | None = None
    duration_text: str = ""           # "4 years"
    confidence: float = 0.0
    calculation_type: str = ""        # "weekday_before_date", "duration_ago"
    raw_expression: str = ""
```

#### Duration Calculation

```python
def calculate_duration_between(self, start_date, end_date):
    """Returns human-readable duration."""
    # "4 years", "2 months", "3 weeks", "5 days"
```

---

### theta_gamma_coupling.py - Neural Oscillation Memory

**Location**: `src/memmachine/wave_memory/theta_gamma_coupling.py`

**Purpose**: Brain-inspired memory binding using neural oscillation patterns.

#### Neuroscience Background

| Rhythm | Frequency | Function |
|--------|-----------|----------|
| **Theta** | 4-8 Hz | "Carrier wave" - temporal context, each cycle = ~125ms memory packet |
| **Gamma** | 30-80 Hz | "Content signal" - nested within theta, ~7 items per theta cycle |
| **Ripples** | 150-250 Hz | Memory consolidation during rest/sleep |

#### Phase Constants

```python
THETA_FREQUENCY_HZ = 6.0              # 167ms per cycle
THETA_CYCLE_MS = 1000 / 6.0           # ~167ms

FAST_GAMMA_HZ = 65.0                  # Encoding gamma
SLOW_GAMMA_HZ = 40.0                  # Retrieval gamma
GAMMA_CYCLES_PER_THETA = 7            # ~7 items per memory packet

ENCODING_PHASE = (0.0, 0.25)          # Theta peak - optimal for LTP
RETRIEVAL_PHASE = (0.5, 0.75)         # Theta trough - optimal for recall
BINDING_PHASE = (0.25, 0.5)           # Rising phase - associative binding
```

#### ThetaPhase Class

```python
@dataclass
class ThetaPhase:
    phase: float = 0.0          # Radians [0, 2*pi]
    cycle_number: int = 0       # Which theta cycle
    amplitude: float = 1.0

    @classmethod
    def from_timestamp(cls, ts, reference):
        """Compute theta phase from timestamp."""
        delta_ms = (ts - reference).total_seconds() * 1000
        cycle = int(delta_ms / THETA_CYCLE_MS)
        phase_radians = (delta_ms % THETA_CYCLE_MS) / THETA_CYCLE_MS * 2 * pi
        return cls(phase=phase_radians, cycle_number=cycle)

    def phase_coherence(self, other):
        """High coherence = memories encoded at similar phases."""
        return (cos(phase_difference) + 1) / 2  # [0, 1]
```

#### GammaBurst Class

```python
@dataclass
class GammaBurst:
    slot: int = 0                    # Which slot [0-6] in theta cycle
    frequency: float = SLOW_GAMMA_HZ # Fast = encoding, slow = retrieval
    power: float = 1.0
```

#### Wave Dimension to Gamma Slot Mapping

```python
# Different wave dimensions map to different gamma slots:
# Slot 0: Temporal (when)
# Slot 1: Entity (who)
# Slot 2: Action (what doing)
# Slot 3: Relational (relationships)
# Slot 4: State (conditions)
# Slot 5: Spatial (where)
# Slot 6: Emotional (how feels)
```

#### ThetaGammaCoupledRetriever

```python
class ThetaGammaCoupledRetriever:
    """Memory retrieval using theta-gamma phase coupling."""

    def retrieve(self, query_wave, top_k, base_scores):
        # 1. Compute query theta phase
        query_theta = ThetaPhase.from_timestamp(query_wave.timestamp, ref_time)

        # 2. Score memories by coupling
        for wave, osc_state in self._memories:
            coupling_score = self._compute_coupling_score(query_osc, osc_state)
            consolidation = osc_state.ripple_count * consolidation_bonus
            ltp_bonus = osc_state.co_activation_strength * 0.1
            total = base_score + coupling_score + consolidation + ltp_bonus

        # 3. Record co-activation (LTP)
        self._record_co_activation(top_results)

        return scored[:top_k]
```

---

### dialogue_linker.py - Cross-Message Temporal Binding

**Location**: `src/memmachine/neural_graph/dialogue_linker.py`

**Purpose**: Links messages across speakers to capture distributed meaning.

#### The Binding Problem

```
USER: "I went to Paris yesterday"
ASSISTANT: "That sounds amazing! How was the Eiffel Tower?"
USER: "It was beautiful at sunset"

"It" → "Eiffel Tower" → "Paris" → "yesterday"

The MEANING is distributed across the dialogue.
```

#### Architecture

```
Layer 2: DIALOGUE AGGREGATORS (Hidden)
┌─────────────────────────────────────────────────────────┐
│  [Exchange]     [Topic:Paris]     [Coref:it→Tower]      │
└──────┼───────────────┼───────────────────┼──────────────┘
       │               │                   │
       ▼               ▼                   ▼
Layer 1: MESSAGE NODES
┌─────────────────────────────────────────────────────────┐
│  [User:went Paris] → [Asst:Eiffel Tower?] → [User:It]   │
└─────────────────────────────────────────────────────────┘
```

#### Aggregator Types

```python
class AggregatorType(Enum):
    EXCHANGE = "exchange"           # Question-Answer pair
    TOPIC_THREAD = "topic_thread"   # Messages about same topic
    COREF_CHAIN = "coref_chain"     # Coreference resolution
    TEMPORAL_ANCHOR = "temporal"    # Temporal context group
    SPEAKER_TURN = "speaker_turn"   # All messages in one turn
    REACTION = "reaction"           # Response to specific content
```

#### Temporal Markers

```python
TEMPORAL_MARKERS = {
    "yesterday": -1,
    "today": 0,
    "tomorrow": 1,
    "last week": -7,
    "last month": -30,
    "last year": -365,
    ...
}
```

#### DialogueLinker Class

```python
class DialogueLinker:
    """Creates and manages dialogue aggregators and cross-message bindings."""

    async def process_dialogue_sequence(self, messages, session_key):
        # 1. Create EXCHANGE aggregators (Q-A pairs)
        exchange_aggs = await self._create_exchange_aggregators(messages, session_key)

        # 2. Create TOPIC THREAD aggregators
        topic_aggs = await self._create_topic_aggregators(messages, session_key)

        # 3. Create COREFERENCE bindings ("it" → "Eiffel Tower")
        coref_bindings = await self._create_coreference_bindings(messages)

        # 4. Create RESPONSE bindings
        response_bindings = await self._create_response_bindings(messages)

        # 5. Create TEMPORAL ANCHOR aggregators
        temporal_aggs = await self._create_temporal_anchors(messages, session_key)
```

---

### date_aware_reranker.py - Temporal BM25 Boosting

**Location**: `src/memmachine/common/reranker/date_aware_reranker.py`

**Purpose**: Boosts BM25 scores when date tokens match.

#### Date Patterns

```python
DATE_PATTERNS = [
    r"\b(January|February|...|December)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?\b",
    r"\b(Monday|Tuesday|...|Sunday)\b",
    r"\b\d{4}-\d{2}-\d{2}\b",           # ISO dates
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",     # US format
    r"\b(19|20)\d{2}\b",                 # Years
    r"\b(yesterday|today|tomorrow)\b",
]

TEMPORAL_KEYWORDS = [
    "when", "date", "time", "day", "week", "month", "year",
    "yesterday", "today", "tomorrow", "last", "next", "ago",
]
```

#### Scoring Algorithm

```python
class DateAwareBM25Reranker(Reranker):
    async def score(self, query, candidates):
        # 1. Check if query is temporal
        is_temporal_query = self._is_temporal_query(query)

        # 2. Extract date tokens
        query_date_tokens = self._extract_date_tokens(query)

        # 3. Standard BM25 scoring
        base_scores = bm25.get_scores(tokenized_query)

        # 4. Apply date token boosting
        for candidate in candidates:
            candidate_date_tokens = self._extract_date_tokens(candidate)

            if query_date_tokens & candidate_date_tokens:
                boost *= date_boost ** len(matching_tokens)

            if is_temporal_query and candidate_date_tokens:
                boost *= temporal_query_boost

        return boosted_scores
```

#### Configuration

```python
@dataclass
class DateAwareBM25RerankerParams:
    k1: float = 1.5
    b: float = 0.75
    epsilon: float = 0.25
    date_boost: float = 2.5            # Multiplier for date matches
    temporal_query_boost: float = 1.5  # Extra boost for temporal queries
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MESSAGE INGESTION                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. temporal_resolver.py                                                 │
│     ├── Parse session datetime                                           │
│     ├── Extract temporal markers ("yesterday", "last week")              │
│     └── Enrich content with resolved dates                               │
│                                                                          │
│  2. temporal.py                                                          │
│     ├── Create temporal edges (BEFORE/AFTER)                            │
│     └── Build temporal chains                                            │
│                                                                          │
│  3. dialogue_linker.py                                                   │
│     ├── Create exchange aggregators                                      │
│     ├── Create temporal anchor aggregators                               │
│     └── Create coreference bindings                                      │
│                                                                          │
│  4. theta_gamma_coupling.py                                              │
│     ├── Compute theta phase from timestamp                               │
│     └── Assign gamma bursts for wave dimensions                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           QUERY TIME                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. tesseract.py                                                         │
│     ├── detect_query_type() → {temporal: 0.6, entity: 0.3, ...}         │
│     └── Route to specialized stores based on type                        │
│                                                                          │
│  2. TemporalStore retrieval                                              │
│     ├── Extract temporal keywords from query                             │
│     ├── Compute temporal charge (keyword match + recency decay)          │
│     └── Weight semantic similarity lower (0.3)                           │
│                                                                          │
│  3. temporal_calculator.py                                               │
│     └── Calculate dates from complex expressions                         │
│                                                                          │
│  4. date_aware_reranker.py                                               │
│     └── Boost scores for date token matches                              │
│                                                                          │
│  5. theta_gamma_coupling.py                                              │
│     ├── Compute phase coherence with query                               │
│     └── Apply consolidation and LTP bonuses                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Algorithms

### 1. Temporal Charge Computation

```python
total_charge = (
    semantic_similarity * 0.3 +      # Lower weight for semantic
    temporal_keyword_match * 0.4 +   # HIGH weight for temporal keywords
    grounded_date_match * 0.3 +      # Bonus for resolved dates
    recency_decay * 0.2              # exp(-days / decay_constant)
)
```

### 2. LTP (Long-Term Potentiation)

```python
# When memories are retrieved together:
edge.ltp_boost += activation_amount
edge.effective_weight = edge.base_weight * edge.ltp_boost

# Daily decay:
edge.ltp_boost = 1.0 + (edge.ltp_boost - 1.0) * exp(-decay_rate * days)
```

### 3. STDP (Spike-Timing Dependent Plasticity)

```python
if time_delta > 0:  # Source fired before target (causal)
    edge.ltp_boost += 0.1 * exp(-time_delta / tau)  # Strengthen
else:               # Target fired before source (anti-causal)
    edge.ltp_boost -= 0.05 * exp(time_delta / tau)  # Weaken
```

### 4. Theta-Gamma Phase Coupling

```python
coupling_score = (
    phase_coherence_weight * theta_phase_coherence +
    cycle_proximity_weight * theta_cycle_proximity +
    0.1 * gamma_slot_overlap +
    0.1 * phase_amplitude_coupling
)
```

---

## Configuration

### TemporalConfig (temporal.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_time_gap_hours` | 24.0 | Max gap for automatic linking |
| `min_propagation_delay_ms` | 10.0 | Minimum edge delay |
| `max_propagation_delay_ms` | 10000.0 | Maximum edge delay |
| `summation_window_hours` | 24.0 | Window for temporal summation |
| `summation_threshold` | 0.5 | Threshold for node "firing" |
| `ltp_activation_threshold` | 2 | Min activations for LTP |
| `ltp_max_boost` | 3.0 | Maximum LTP multiplier |
| `ltp_decay_rate` | 0.01 | Daily decay rate |

### StoreConfig (tesseract.py)

| Store | semantic_weight | temporal_decay | entity_boost |
|-------|-----------------|----------------|--------------|
| TemporalStore | 0.3 | 14 days | 0.3 |
| EntityStore | 0.4 | - | 0.6 |
| ReasoningStore | 0.5 | - | 0.4 |
| AdversarialStore | 0.4 | - | 0.5 |

### ThetaGammaCoupledRetriever

| Parameter | Default | Description |
|-----------|---------|-------------|
| `phase_coherence_weight` | 0.15 | Weight for theta phase similarity |
| `cycle_proximity_weight` | 0.10 | Weight for temporal proximity |
| `consolidation_bonus` | 0.05 | Bonus per ripple replay |

---

## Summary

MemMachine's temporal architecture combines multiple complementary approaches:

1. **Symbolic Date Resolution** (temporal_resolver.py, temporal_calculator.py)
   - Rule-based conversion of relative → absolute dates
   - Pattern matching for complex expressions

2. **Neural-Inspired Dynamics** (temporal.py, theta_gamma_coupling.py)
   - LTP for frequently co-activated memories
   - Theta-gamma coupling for temporal binding
   - Refractory periods for stability

3. **Multi-Store Retrieval** (tesseract.py)
   - Specialized stores for different query types
   - Dynamic weighting based on query analysis

4. **Cross-Message Linking** (dialogue_linker.py)
   - Aggregators for temporal anchors
   - Coreference resolution across time

5. **Retrieval Boosting** (date_aware_reranker.py)
   - BM25 score enhancement for date matches

This layered approach enables accurate answers to questions like:
- "When did Caroline paint?" → Resolves "yesterday" to actual date
- "What happened 4 years ago?" → Calculates date, finds relevant memories
- "How long have they known each other?" → Duration calculation from memories

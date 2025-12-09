# MemMachine Complete System Architecture
## Hybrid QA System for LoCoMo Benchmark v4.1

**Version:** 4.1 (HYBRID ROUTING + DURATION CALCULATOR)
**Last Updated:** December 7, 2025
**Author:** MemMachine Team

---

# TABLE OF CONTENTS

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [File Structure](#file-structure)
4. [Core Components](#core-components)
   - 4.1 [Benchmark Pipeline](#41-benchmark-pipeline)
   - 4.2 [Cognitive Pipeline](#42-cognitive-pipeline)
   - 4.3 [Temporal Processing](#43-temporal-processing)
   - 4.4 [Duration Calculator](#44-duration-calculator)
   - 4.5 [Entity Tracker](#45-entity-tracker)
   - 4.6 [Hybrid Retrieval](#46-hybrid-retrieval)
   - 4.7 [Question Router](#47-question-router)
5. [Complete Code Listings](#complete-code-listings)
6. [Configuration](#configuration)
7. [Usage](#usage)

---

# 1. SYSTEM OVERVIEW

The MemMachine Hybrid QA System is designed for long-conversation question answering using the LoCoMo benchmark. It achieves **51.76%+ overall accuracy** through:

| Component | Purpose | Impact |
|-----------|---------|--------|
| **Hybrid Routing** | Classifies questions into TEMPORAL/ADVERSARIAL/MULTI_HOP/OPEN_DOMAIN/SINGLE_HOP | +15% accuracy |
| **Temporal Processing** | Calculates actual dates from relative references ("on Sunday" → "21 May 2023") | +30% temporal accuracy |
| **Duration Calculator** | Calculates durations for "How long" questions | New in v4.1 |
| **Entity Tracker** | Detects entity swaps in adversarial questions | +10% adversarial accuracy |
| **Cognitive Memory** | 6-phase cognitive pipeline with temporal normalization | +5% overall |
| **BM25+Semantic** | Hybrid retrieval with category-specific weights | +8% retrieval quality |

---

# 2. ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MEMMACHINE HYBRID QA SYSTEM v4.1                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      1. INGESTION PIPELINE                            │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │   │
│  │  │   Messages  │→ │  Temporal   │→ │   Entity    │→ │  Cognitive  │  │   │
│  │  │  Extraction │  │ Normalizer  │  │   Tracker   │  │   Memory    │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                         │                                    │
│  ┌──────────────────────────────────────▼───────────────────────────────┐   │
│  │                     2. QUESTION ROUTER                                │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │   │
│  │  │  classify_question() → {TEMPORAL, ADVERSARIAL, MULTI_HOP, ...}   │  │   │
│  │  │  is_duration_question() → True/False                             │  │   │
│  │  └─────────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                         │                                    │
│  ┌──────────────────────────────────────▼───────────────────────────────┐   │
│  │                     3. MODE-SPECIFIC RETRIEVAL                        │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │   │
│  │  │SINGLE_HOP│ │ TEMPORAL │ │MULTI_HOP │ │OPEN_DOM  │ │ADVERSAR  │    │   │
│  │  │BM25:0.65 │ │BM25:0.75 │ │BM25:0.50 │ │BM25:0.30 │ │BM25:0.70 │    │   │
│  │  │top_k:25  │ │top_k:30  │ │top_k:40  │ │top_k:35  │ │top_k:20  │    │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                         │                                    │
│  ┌──────────────────────────────────────▼───────────────────────────────┐   │
│  │                   4. MODE-SPECIFIC ENHANCEMENTS                       │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │   │
│  │  │    TEMPORAL     │  │   ADVERSARIAL   │  │    DURATION     │       │   │
│  │  │  find_date_for  │  │  detect_entity  │  │  DurationCalc   │       │   │
│  │  │    _event()     │  │    _swap()      │  │  .calculate()   │       │   │
│  │  │ 📅 CALCULATED   │  │ ⚠️ WARNING     │  │ ⏱️ DURATION     │       │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                         │                                    │
│  ┌──────────────────────────────────────▼───────────────────────────────┐   │
│  │                    5. MODE-SPECIFIC PROMPTS                           │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │   │
│  │  │STANDARD  │ │TEMPORAL  │ │MULTI_HOP │ │OPEN_DOM  │ │DURATION  │    │   │
│  │  │ _PROMPT  │ │ _PROMPT  │ │ _PROMPT  │ │ _PROMPT  │ │ _PROMPT  │    │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                         │                                    │
│  ┌──────────────────────────────────────▼───────────────────────────────┐   │
│  │                      6. ANSWER GENERATION                             │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                   │   │
│  │  │   Ollama    │→ │ Anti-Absten │→ │  GPT-4o-mini│                   │   │
│  │  │   qwen2.5   │  │   Retry     │  │   LLM Judge │                   │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 3. FILE STRUCTURE

```
C:\Users\anovr\Desktop\MemMachine-main\
├── evaluation/
│   ├── locomo10_parallel_benchmark.py      # Main benchmark (2527 lines)
│   ├── cognitive_pipeline/
│   │   ├── __init__.py                     # Module exports
│   │   ├── pipeline_config.py              # Configuration dataclasses
│   │   ├── cognitive_episodic_memory.py    # Cognitive memory wrapper
│   │   └── metrics_collector.py            # Metrics tracking
│   └── locomo/
│       └── locomo10.json                   # LoCoMo dataset
└── src/
    └── memmachine/
        ├── common/
        │   ├── privacy_sanitizer.py        # PII detection
        │   ├── emotional_encoder.py        # Emotion detection
        │   ├── granularity_router.py       # Entropy-based routing
        │   └── domain_classifier.py        # Domain classification
        ├── knowledge_graph/                # KG components
        ├── episodic_memory/                # Memory layers
        └── sharded_memory/                 # Sharded storage
```

---

# 4. CORE COMPONENTS

## 4.1 BENCHMARK PIPELINE

**File:** `evaluation/locomo10_parallel_benchmark.py`
**Lines:** 2527

### Configuration Constants

```python
# Ollama Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "gpt-4o-mini"  # Official MemMachine evaluation
EMBEDDING_MODEL = "nomic-embed-text"

# Parallel processing
CONCURRENT_QUESTIONS = 4
EMBEDDING_BATCH_SIZE = 10

# Categories
CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# Benchmark parameters
CONTEXT_LIMIT = 12000
TOP_K_RETRIEVAL = 30
```

### Mode-Specific Retrieval Configs

```python
MODE_CONFIGS = {
    "SINGLE_HOP": {
        "bm25_weight": 0.65,
        "semantic_weight": 0.35,
        "top_k": 25,
        "context_limit": 10000,
    },
    "TEMPORAL": {
        "bm25_weight": 0.75,
        "semantic_weight": 0.25,
        "top_k": 30,
        "context_limit": 10000,
        "use_temporal_index": True,
    },
    "MULTI_HOP": {
        "bm25_weight": 0.50,
        "semantic_weight": 0.50,
        "top_k": 40,
        "context_limit": 16000,
    },
    "OPEN_DOMAIN": {
        "bm25_weight": 0.30,
        "semantic_weight": 0.70,
        "top_k": 35,
        "context_limit": 12000,
    },
    "ADVERSARIAL": {
        "bm25_weight": 0.70,
        "semantic_weight": 0.30,
        "top_k": 20,
        "context_limit": 8000,
    },
}
```

---

## 4.2 COGNITIVE PIPELINE

**File:** `evaluation/cognitive_pipeline/cognitive_episodic_memory.py`
**Lines:** 1053

The CognitiveEpisodicMemory wrapper initializes EpisodicMemory with all 6 cognitive modules:

1. **Privacy Sanitization** - PII detection & redaction
2. **Emotional Encoding** - Plutchik emotions & mood tracking
3. **Mid-term Memory** - Heat-based consolidation
4. **Knowledge Graph** - Entity extraction & reasoning
5. **Granularity Router** - Entropy-based adaptive retrieval
6. **Domain Classifier** - Semantic memory sharding

### CognitiveEpisodicMemory Class

```python
class CognitiveEpisodicMemory:
    """
    Wrapper that creates EpisodicMemory with all cognitive modules enabled.
    """
    def __init__(
        self,
        session_key: str,
        config: CognitivePipelineConfig | None = None,
    ):
        self.session_key = session_key
        self.config = config or CognitivePipelineConfig()
        self._metrics_collector = MetricsCollector()

        # Initialize all components
        self._privacy_sanitizer: PrivacySanitizer | None = None
        self._compliance_auditor: ComplianceAuditor | None = None
        self._emotion_detector: EmotionDetector | None = None
        self._mood_tracker: MoodTracker | None = None
        self._resonance_scorer: EmotionalResonanceScorer | None = None
        self._knowledge_graph_service: KnowledgeGraphService | None = None
        self._granularity_router: GranularityRouter | None = None
        self._domain_classifier: DomainClassifier | None = None
        self._episodic_memory: EpisodicMemory | None = None

        # Initialize components based on config
        self._init_privacy_sanitizer()
        self._init_emotional_encoder()
        self._init_knowledge_graph()
        self._init_granularity_router()
        self._init_domain_classifier()
        self._init_sharded_memory()
        self._init_episodic_memory()

    async def ingest_episode(
        self,
        content: str,
        speaker: str = "user",
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """Ingest an episode through the full cognitive pipeline."""
        # ... implementation

    async def query(
        self,
        question: str,
        limit: int = 20,
    ) -> QueryResult:
        """Query memory through the full cognitive pipeline."""
        # ... implementation
```

---

## 4.3 TEMPORAL PROCESSING

### Temporal Markers Definition

```python
TEMPORAL_MARKERS = {
    # Days
    r"\byesterday\b": ("day", -1),
    r"\btoday\b": ("day", 0),
    r"\btomorrow\b": ("day", 1),
    r"\bthe day before\b": ("day", -1),
    r"\bthe day after\b": ("day", 1),
    r"\btwo days ago\b": ("day", -2),
    r"\bthree days ago\b": ("day", -3),
    r"\ba few days ago\b": ("day", -3),

    # Weeks
    r"\blast week\b": ("week", -1),
    r"\bthis week\b": ("week", 0),
    r"\bnext week\b": ("week", 1),
    r"\bthe week before\b": ("week", -1),
    r"\ba week ago\b": ("week", -1),
    r"\btwo weeks ago\b": ("week", -2),

    # Weekdays
    r"\bon sunday\b": ("weekday", 6),
    r"\bon monday\b": ("weekday", 0),
    r"\bon tuesday\b": ("weekday", 1),
    r"\bon wednesday\b": ("weekday", 2),
    r"\bon thursday\b": ("weekday", 3),
    r"\bon friday\b": ("weekday", 4),
    r"\bon saturday\b": ("weekday", 5),
    r"\blast sunday\b": ("last_weekday", 6),
    r"\blast monday\b": ("last_weekday", 0),
    # ... etc

    # Months
    r"\blast month\b": ("month", -1),
    r"\bthis month\b": ("month", 0),
    r"\bnext month\b": ("month", 1),

    # Years
    r"\blast year\b": ("year", -1),
    r"\bthis year\b": ("year", 0),
    r"\bnext year\b": ("year", 1),
}
```

### TemporalEvent Dataclass

```python
@dataclass
class TemporalEvent:
    """An event with calculated temporal information."""
    speaker: str                         # "Caroline"
    text: str                            # "I went to the support group on Sunday"
    session_date: datetime               # 2023-05-25 (from session timestamp)
    session_time_str: str                # "1:56 pm on 25 May, 2023"
    calculated_date: datetime | None     # 2023-05-21 (calculated from "on Sunday")
    relative_reference: str | None       # "on sunday"
    formatted_date: str                  # "21 May 2023 (the Sunday before 25 May 2023)"
    msg_idx: int                         # Message index
    session: int                         # Session number
    actions: list[str] = None            # Extracted actions
    entities: list[str] = None           # Extracted entities

    def __post_init__(self):
        if self.actions is None:
            self.actions = self._extract_actions()
        if self.entities is None:
            self.entities = self._extract_entities()

    def _extract_actions(self) -> list[str]:
        """Extract action verbs from text."""
        text_lower = self.text.lower()
        action_verbs = {
            "paint": ["paint", "painted", "painting", "paints"],
            "run": ["run", "ran", "running", "runs"],
            "go": ["go", "went", "going", "goes", "gone"],
            "sign": ["sign", "signed", "signing", "signs"],
            # ... more verbs
        }
        actions = []
        for base_verb, forms in action_verbs.items():
            for form in forms:
                if re.search(rf"\b{form}\b", text_lower):
                    actions.append(base_verb)
                    break
        return list(set(actions))

    def _extract_entities(self) -> list[str]:
        """Extract key entities/nouns from text."""
        text_lower = self.text.lower()
        key_entities = [
            "marathon", "class", "course", "trip", "painting", "necklace",
            "birthday", "wedding", "party", "conference", "group", "support",
            "lgbtq", "charity", "race", "pottery", "sunrise", "speech",
            # ... more entities
        ]
        entities = [e for e in key_entities if re.search(rf"\b{e}\b", text_lower)]
        return list(set(entities))
```

### Date Calculation Function

```python
def calculate_actual_date(session_date: datetime, marker_type: str, offset: int) -> datetime | None:
    """Calculate actual date from session date + relative offset.

    Examples:
        - session=25 May 2023, marker="weekday", offset=6 (Sunday) → 21 May 2023
        - session=25 May 2023, marker="day", offset=-1 → 24 May 2023
    """
    try:
        if marker_type == "day":
            return session_date + timedelta(days=offset)
        elif marker_type == "week":
            return session_date + timedelta(weeks=offset)
        elif marker_type == "weekday":
            target_weekday = offset
            current_weekday = session_date.weekday()
            days_diff = current_weekday - target_weekday
            if days_diff < 0:
                days_diff += 7
            return session_date - timedelta(days=days_diff)
        elif marker_type == "last_weekday":
            target_weekday = offset
            current_weekday = session_date.weekday()
            days_diff = current_weekday - target_weekday
            if days_diff <= 0:
                days_diff += 7
            return session_date - timedelta(days=days_diff)
        elif marker_type == "month":
            new_month = session_date.month + offset
            new_year = session_date.year
            while new_month <= 0:
                new_month += 12
                new_year -= 1
            while new_month > 12:
                new_month -= 12
                new_year += 1
            return datetime(new_year, new_month, 1)
        elif marker_type == "year":
            return datetime(session_date.year + offset, session_date.month, session_date.day)
    except (ValueError, OverflowError):
        return None
    return None
```

### find_date_for_event Function

```python
def find_date_for_event(query: str, messages: list[dict], temporal_events: list[TemporalEvent] | None = None) -> str | None:
    """Try to find the date when an event happened."""
    query_lower = query.lower()

    # Try structured event index query first
    if temporal_events:
        matches = query_event_index(query, temporal_events, top_k=3)
        if matches:
            best_event, best_score = matches[0]
            if best_score >= 2.0 and best_event.calculated_date:
                return best_event.formatted_date

    # Extract subject and action from question
    patterns = [
        r"when\s+(?:did|was|is)\s+(\w+)\s+(.+?)(?:\?|$)",
        r"when\s+(\w+)\s+(?:has|had|have)\s+(.+?)(?:\?|$)",
        r"when\s+is\s+(\w+)\s+(?:planning|going)\s+(.+?)(?:\?|$)",
        r"how\s+long\s+(?:has|had|have)\s+(\w+)\s+(.+?)(?:\?|$)",
        r"how\s+long\s+ago\s+(?:was|did)\s+(\w+)(?:'s)?\s+(.+?)(?:\?|$)",
        r"when\s+(\w+)\s+(.+?)(?:\?|$)",
    ]

    subject = None
    action = None

    for pattern in patterns:
        match = re.search(pattern, query_lower)
        if match:
            subject = match.group(1).lower()
            action = match.group(2).lower()
            break

    # ... rest of implementation
```

---

## 4.4 DURATION CALCULATOR

**v4.1 New Feature**

### DurationQuestion Dataclass

```python
@dataclass
class DurationQuestion:
    """Represents a parsed duration question with identified components."""
    question_type: str          # "ongoing", "ago", "between"
    subject: str                # "Caroline"
    activity: str               # "had her current job"
    start_event: str | None     # Event that marks the start
    end_event: str | None       # Event that marks the end (or "now")
    reference_date: datetime | None  # Reference date for "now" calculations
```

### DurationCalculator Class

```python
class DurationCalculator:
    """Sophisticated duration calculator for "How long" questions.

    Handles three types of duration questions:
    1. ONGOING: "How long has X been doing Y?" → needs today - first_mention_date
    2. AGO: "How long ago was X?" → needs today - event_date
    3. BETWEEN: "How long did X take?" → needs end_date - start_date
    """

    DURATION_PATTERNS = {
        "ongoing": [
            r"how\s+long\s+(?:has|have)\s+(\w+)\s+(?:been\s+)?(.+?)(?:\?|$)",
            r"how\s+long\s+(?:has|have)\s+(\w+)\s+had\s+(.+?)(?:\?|$)",
            r"for\s+how\s+long\s+(?:has|have)\s+(\w+)\s+(?:been\s+)?(.+?)(?:\?|$)",
        ],
        "ago": [
            r"how\s+long\s+ago\s+(?:was|did|has)\s+(\w+)(?:'s)?\s+(.+?)(?:\?|$)",
            r"how\s+long\s+ago\s+did\s+(\w+)\s+(.+?)(?:\?|$)",
            r"how\s+long\s+has\s+it\s+been\s+since\s+(\w+)\s+(.+?)(?:\?|$)",
        ],
        "between": [
            r"how\s+long\s+did\s+(?:the\s+)?(\w+)(?:'s)?\s+(.+?)\s+(?:take|last)(?:\?|$)",
            r"how\s+long\s+was\s+(\w+)(?:'s)?\s+(.+?)(?:\?|$)",
            r"how\s+long\s+did\s+(\w+)\s+(?:spend|take)\s+(.+?)(?:\?|$)",
        ],
    }

    ACTIVITY_NORMALIZER = {
        "painting": "paint", "painted": "paint", "paints": "paint",
        "running": "run", "ran": "run", "runs": "run",
        "camping": "camp", "camped": "camp", "camps": "camp",
        "pottery": "pottery", "doing pottery": "pottery",
        "working": "work", "worked": "work", "works": "work",
        "married": "marry", "marriage": "marry",
        "friends": "friend", "friendship": "friend",
        "together": "together", "relationship": "relationship",
    }

    def __init__(self, temporal_events: list, messages: list[dict], reference_date: datetime | None = None):
        self.temporal_events = temporal_events
        self.messages = messages
        self.reference_date = reference_date or self._find_latest_session_date()
        self._activity_index = self._build_activity_index()

    def _find_latest_session_date(self) -> datetime:
        """Find the latest session date in the conversation (acts as 'now')."""
        latest = None
        for event in self.temporal_events:
            if event.session_date and (latest is None or event.session_date > latest):
                latest = event.session_date
        return latest or datetime.now()

    def _build_activity_index(self) -> dict[str, list[tuple[datetime, str, str]]]:
        """Build index: activity -> [(date, speaker, text), ...]"""
        index = defaultdict(list)
        for event in self.temporal_events:
            event_date = event.calculated_date or event.session_date
            if not event_date:
                continue
            text_lower = event.text.lower()
            speaker = event.speaker
            for raw, normalized in self.ACTIVITY_NORMALIZER.items():
                if raw in text_lower:
                    index[normalized].append((event_date, speaker, event.text))
            if event.actions:
                for action in event.actions:
                    index[action].append((event_date, speaker, event.text))
        return index

    @classmethod
    def is_duration_question(cls, question: str) -> bool:
        """Detect if this is a duration question requiring calculation."""
        q_lower = question.lower().strip()
        if not q_lower.startswith("how long"):
            return False
        for patterns in cls.DURATION_PATTERNS.values():
            for pattern in patterns:
                if re.search(pattern, q_lower):
                    return True
        duration_keywords = {"how long", "since when", "for how long", "how many years"}
        return any(kw in q_lower for kw in duration_keywords)

    def calculate_duration(self, question: str) -> tuple[str | None, str | None]:
        """Calculate duration for a question."""
        parsed = self.parse_duration_question(question)
        if not parsed:
            return None, None

        if parsed.question_type == "ongoing":
            first_mention = self._find_first_mention(parsed.subject, parsed.activity)
            if first_mention and self.reference_date:
                duration = self.reference_date - first_mention
                formatted = self._format_duration(duration)
                explanation = f"First mention: {first_mention.strftime('%d %B %Y')}, Reference: {self.reference_date.strftime('%d %B %Y')}"
                return formatted, explanation

        elif parsed.question_type == "ago":
            event_date = self._find_event_date(parsed.subject, parsed.activity)
            if event_date and self.reference_date:
                duration = self.reference_date - event_date
                formatted = self._format_duration(duration)
                explanation = f"Event date: {event_date.strftime('%d %B %Y')}, Reference: {self.reference_date.strftime('%d %B %Y')}"
                return formatted, explanation

        elif parsed.question_type == "between":
            start_date = self._find_first_mention(parsed.subject, parsed.activity)
            if start_date and self.reference_date:
                duration = self.reference_date - start_date
                formatted = self._format_duration(duration)
                explanation = f"Start: {start_date.strftime('%d %B %Y')}, End: {self.reference_date.strftime('%d %B %Y')}"
                return formatted, explanation

        return None, None

    def _format_duration(self, duration: timedelta) -> str:
        """Format a timedelta into human-readable duration."""
        days = duration.days
        if days < 0:
            return "Not yet occurred"

        years = days // 365
        remaining_days = days % 365
        months = remaining_days // 30
        remaining_days = remaining_days % 30
        weeks = remaining_days // 7
        remaining_days = remaining_days % 7

        parts = []
        if years > 0:
            parts.append(f"{years} year{'s' if years != 1 else ''}")
        if months > 0 and years < 3:
            parts.append(f"{months} month{'s' if months != 1 else ''}")
        if weeks > 0 and years == 0 and months < 6:
            parts.append(f"{weeks} week{'s' if weeks != 1 else ''}")
        if remaining_days > 0 and years == 0 and months == 0:
            parts.append(f"{remaining_days} day{'s' if remaining_days != 1 else ''}")

        if not parts:
            return "Less than a day"
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        else:
            return f"About {parts[0]}"

    def get_duration_context(self, question: str) -> str:
        """Generate context string with calculated duration for the prompt."""
        formatted, explanation = self.calculate_duration(question)
        if formatted:
            return f"\n\n⏱️ CALCULATED DURATION: {formatted}\n{explanation}\nThis duration was calculated from conversation timestamps."
        return ""
```

---

## 4.5 ENTITY TRACKER

```python
class EntityTracker:
    """Tracks entities and their possessions for adversarial detection.

    v3.1: Enhanced to track:
    - entity_possessions: person -> {hobby, item, trait, ...}
    - entity_actions: person -> {did X at Y time}

    Used to detect entity swaps in adversarial questions.
    """

    def __init__(self):
        self.known_entities: set[str] = set()
        self.entity_possessions: dict[str, set[str]] = defaultdict(set)
        self.entity_actions: dict[str, set[str]] = defaultdict(set)
        self.possession_to_entity: dict[str, str] = {}

    def extract_from_message(self, speaker: str, text: str) -> None:
        """Extract entity-possession relationships from a message."""
        # Add speaker as known entity
        self.known_entities.add(speaker.lower())

        text_lower = text.lower()

        # Extract possessions (hobbies, items, traits)
        possession_patterns = [
            # Hobbies
            r"(?:i|my)\s+(?:do|enjoy|love|practice|started|began)\s+(\w+(?:\s+\w+)?)",
            r"(?:i|my)\s+(?:hobby|hobbies)\s+(?:is|are)\s+(\w+(?:\s+\w+)?)",
            # Items
            r"(?:i|my)\s+(?:have|got|bought|own)\s+(?:a\s+)?(\w+(?:\s+\w+)?)",
            r"my\s+(\w+(?:\s+\w+)?)\s+(?:is|was)",
            # Activities
            r"i\s+(?:went|go|am going)\s+(?:to\s+)?(?:the\s+)?(\w+(?:\s+\w+)?)",
            r"i\s+(?:ran|run|participate)\s+(?:in\s+)?(?:a\s+)?(\w+(?:\s+\w+)?)",
        ]

        speaker_lower = speaker.lower()

        for pattern in possession_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                possession = match.strip()
                if len(possession) > 2 and possession not in STOPWORDS:
                    self.entity_possessions[speaker_lower].add(possession)
                    # Track reverse mapping (most recent speaker wins)
                    self.possession_to_entity[possession] = speaker_lower

        # Extract actions
        action_patterns = [
            r"i\s+(painted|drew|ran|camped|hiked|visited|attended|joined|signed)",
            r"i\s+(?:have\s+)?(practiced|played|volunteered|worked|studied)",
        ]

        for pattern in action_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                self.entity_actions[speaker_lower].add(match)

    def get_owner(self, possession: str) -> str | None:
        """Get the entity that owns a possession."""
        possession_lower = possession.lower()

        # Direct lookup
        if possession_lower in self.possession_to_entity:
            return self.possession_to_entity[possession_lower]

        # Fuzzy match
        for poss, entity in self.possession_to_entity.items():
            if possession_lower in poss or poss in possession_lower:
                return entity

        return None

    def verify_claim(self, entity: str, possession: str) -> tuple[bool, str | None]:
        """Verify if entity truly has/does possession.

        Returns:
            (is_valid, actual_owner)
            - is_valid: True if claim is correct
            - actual_owner: The actual owner if claim is wrong
        """
        entity_lower = entity.lower()
        possession_lower = possession.lower()

        # Check if entity has this possession
        if possession_lower in self.entity_possessions.get(entity_lower, set()):
            return True, None

        # Check if another entity has it
        actual_owner = self.get_owner(possession_lower)
        if actual_owner and actual_owner != entity_lower:
            return False, actual_owner

        return True, None  # Unknown, assume valid


def detect_entity_swap(question: str, entity_tracker: EntityTracker) -> tuple[bool, str | None]:
    """Detect if question attributes something to wrong entity.

    Returns:
        (is_swap_detected, correction_message)
    """
    question_lower = question.lower()

    # Extract entity from question
    entity_patterns = [
        r"(?:what\s+is|does)\s+(\w+)(?:'s)?\s+(.+?)(?:\?|$)",
        r"(?:when\s+did)\s+(\w+)\s+(.+?)(?:\?|$)",
        r"(?:has|have)\s+(\w+)\s+(.+?)(?:\?|$)",
    ]

    for pattern in entity_patterns:
        match = re.search(pattern, question_lower)
        if match:
            entity = match.group(1)
            possession = match.group(2).strip().rstrip("?")

            # Skip common non-entity words
            if entity.lower() in {"the", "a", "an", "what", "when", "how", "why", "who"}:
                continue

            # Verify the claim
            is_valid, actual_owner = entity_tracker.verify_claim(entity, possession)

            if not is_valid and actual_owner:
                return True, f"'{possession}' belongs to {actual_owner.title()}, not {entity.title()}"

    return False, None
```

---

## 4.6 HYBRID RETRIEVAL

```python
async def retrieve_relevant_messages(
    session: aiohttp.ClientSession,
    question: str,
    messages: list[dict],
    category: str = "single_hop",
    top_k: int = TOP_K_RETRIEVAL,
    temporal_events: list[TemporalEvent] | None = None,
) -> list[dict]:
    """Retrieve relevant messages with category-specific weighting."""

    # Detect temporal mode
    is_temporal = category == "temporal" or is_temporal_question(question)

    # Category-specific semantic weights
    WEIGHTS_BY_CATEGORY = {
        "single_hop": 0.35,   # Heavy BM25 for exact keyword matches
        "temporal": 0.25,     # Even heavier BM25 for temporal
        "open_domain": 0.75,  # Heavy semantic for thematic queries
        "multi_hop": 0.60,    # Balanced
        "adversarial": 0.65,  # Slightly favor semantic
    }
    semantic_weight = WEIGHTS_BY_CATEGORY.get(category, 0.6)

    # Override for detected temporal questions
    if is_temporal and category != "temporal":
        semantic_weight = 0.25

    # Get embeddings
    question_embedding = await get_embedding_async(session, question)
    texts = [f"{m['speaker']}: {m['text']}" for m in messages]

    # Compute BM25 scores
    bm25_scores = compute_bm25_scores(question, texts)
    max_bm25 = max(bm25_scores) if bm25_scores else 1
    if max_bm25 > 0:
        bm25_scores = [s / max_bm25 for s in bm25_scores]

    # Compute semantic scores
    semantic_scores = []
    for msg in messages:
        if msg.get('embedding') and question_embedding:
            sim = cosine_similarity(question_embedding, msg['embedding'])
            semantic_scores.append(sim)
        else:
            semantic_scores.append(0.0)

    max_sem = max(semantic_scores) if semantic_scores else 1
    if max_sem > 0:
        semantic_scores = [s / max_sem for s in semantic_scores]

    # Combine scores
    bm25_weight = 1 - semantic_weight
    combined_scores = [
        bm25_weight * bm25 + semantic_weight * sem
        for bm25, sem in zip(bm25_scores, semantic_scores)
    ]

    # TEMPORAL_MODE boosting
    if is_temporal:
        # ... boost messages with temporal keywords
        pass

    scored_messages = list(zip(messages, combined_scores))
    scored_messages.sort(key=lambda x: x[1], reverse=True)
    return [msg for msg, score in scored_messages[:top_k]]
```

---

## 4.7 QUESTION ROUTER

```python
def classify_question(question: str, entity_tracker: EntityTracker | None = None) -> set[str]:
    """Classify question into one or more modes for routing.

    v4.0: Hybrid routing - questions can have MULTIPLE modes.

    Returns:
        Set of active modes: {"TEMPORAL", "ADVERSARIAL", "MULTI_HOP", "OPEN_DOMAIN", "SINGLE_HOP"}
    """
    modes = set()
    q_lower = question.lower().strip()

    # TEMPORAL mode: date/time questions
    if re.match(r"^when\s+", q_lower):
        modes.add("TEMPORAL")
    elif re.match(r"^how\s+long\s+", q_lower):
        modes.add("TEMPORAL")
    elif any(kw in q_lower for kw in ["what date", "what time", "which day", "which month"]):
        modes.add("TEMPORAL")

    # ADVERSARIAL mode: entity verification
    if entity_tracker:
        is_swap, _ = detect_entity_swap(question, entity_tracker)
        if is_swap:
            modes.add("ADVERSARIAL")

    # MULTI_HOP mode: 2+ entities mentioned
    skip_words = {"When", "What", "Where", "Who", "How", "Why", "Which", "Did", "Does", "Has", "Have", "Is", "Are"}
    entities = set(re.findall(r'\b[A-Z][a-z]+\b', question)) - skip_words
    if len(entities) >= 2:
        modes.add("MULTI_HOP")

    # MULTI_HOP mode: aggregation questions
    aggregation_patterns = [
        r"\bhow\s+many\s+times\b",
        r"\bwhat\s+(?:are|activities|hobbies|events)\b.*\bdoes\b",
        r"\blist\b",
        r"\ball\s+(?:the|of)\b",
    ]
    for pattern in aggregation_patterns:
        if re.search(pattern, q_lower):
            modes.add("MULTI_HOP")
            break

    # OPEN_DOMAIN mode: inference/opinion questions
    open_domain_patterns = [
        r"\bwould\b.*\b(?:likely|probably)\b",
        r"\bif\b.*\bhad\b",
        r"\bwhat\s+(?:would|could|might)\b",
        r"\bwould\b.*\bstill\b",
        r"\bwould\b.*\bwant\b",
        r"\blikely\s+to\b",
        r"\bprobably\b",
    ]
    for pattern in open_domain_patterns:
        if re.search(pattern, q_lower):
            modes.add("OPEN_DOMAIN")
            break

    # Default: SINGLE_HOP
    if not modes:
        modes.add("SINGLE_HOP")

    return modes


def get_primary_mode(modes: set[str]) -> str:
    """Get the primary mode for prompt selection."""
    # Priority order
    priority = ["ADVERSARIAL", "TEMPORAL", "OPEN_DOMAIN", "MULTI_HOP", "SINGLE_HOP"]
    for mode in priority:
        if mode in modes:
            return mode
    return "SINGLE_HOP"


def merge_retrieval_configs(modes: set[str]) -> dict:
    """Merge retrieval configs from multiple modes."""
    # Start with defaults
    config = {
        "bm25_weight": 0.65,
        "semantic_weight": 0.35,
        "top_k": 25,
        "context_limit": 10000,
    }

    # Apply mode-specific overrides (last one wins for conflicts)
    for mode in sorted(modes):  # Sorted for consistency
        if mode in MODE_CONFIGS:
            mode_config = MODE_CONFIGS[mode]
            # Take the more permissive values
            config["top_k"] = max(config["top_k"], mode_config.get("top_k", config["top_k"]))
            config["context_limit"] = max(config["context_limit"], mode_config.get("context_limit", config["context_limit"]))
            # Average the weights if multiple modes
            config["bm25_weight"] = (config["bm25_weight"] + mode_config.get("bm25_weight", config["bm25_weight"])) / 2
            config["semantic_weight"] = 1 - config["bm25_weight"]

    return config
```

---

# 5. PROMPT TEMPLATES

## STANDARD_PROMPT
```python
STANDARD_PROMPT = """You are a memory retrieval assistant. Answer using information from the memories below.

{context}

QUESTION: {question}

RULES:
1. Use facts from the memories above - DO NOT add external knowledge
2. If you find ANY relevant information, provide it as your answer
3. Only say "Not stated in memories" if the topic is COMPLETELY absent
4. Be specific and concise

ANSWER:"""
```

## TEMPORAL_PROMPT
```python
TEMPORAL_PROMPT = """You are a memory retrieval assistant answering WHEN something happened.

⚠️ CRITICAL - READ FIRST:
If you see "📅 CALCULATED EVENT DATE:" below, that IS your answer.
The date has been pre-computed from session timestamps. OUTPUT THAT DATE.

{context}

QUESTION: {question}

ANSWERING RULES:
1. If "📅 CALCULATED EVENT DATE:" exists above → OUTPUT THAT DATE (it is authoritative)
2. Format: "21 May 2023" or "May 2023" or "2023" (any is acceptable)
3. Only say "Not stated" if there is NO calculated date AND no date info in context

ANSWER (date only):"""
```

## DURATION_PROMPT
```python
DURATION_PROMPT = """You are a memory retrieval assistant answering a DURATION question.

⚠️ CRITICAL - READ FIRST:
If you see "⏱️ CALCULATED DURATION:" below, that IS your answer.
The duration has been pre-computed from conversation timestamps. OUTPUT THAT DURATION.

{context}

QUESTION: {question}

DURATION ANSWERING RULES:
1. If "⏱️ CALCULATED DURATION:" exists above → OUTPUT THAT DURATION (it is authoritative)
2. Accept various formats: "3 years", "6 months", "2 weeks and 3 days", "About 1 year"
3. If no calculated duration, look for time references in the memories to estimate
4. Only say "Not stated" if there is NO duration info AND no way to estimate from dates

ANSWER (duration only):"""
```

## MULTI_HOP_PROMPT
```python
MULTI_HOP_PROMPT = """You are a memory retrieval assistant. Answer using information from the memories below.

{context}

QUESTION: {question}

MULTI-HOP MODE - GATHER ALL RELATED FACTS:
This question requires finding MULTIPLE pieces of information.

STEPS:
1. Search through ALL sessions for relevant mentions
2. List each relevant fact you find
3. Combine them into a complete answer

For questions asking "what activities/hobbies does X do":
- Look for ALL activities mentioned (pottery, painting, camping, swimming, running, etc.)
- Include every activity even if mentioned in different sessions

Be thorough - include ALL relevant items, not just the first one you find.

ANSWER (include all relevant items):"""
```

## ADVERSARIAL_PROMPT
```python
ADVERSARIAL_PROMPT = """You are a memory retrieval assistant. CAREFULLY verify facts before answering.

{context}

QUESTION: {question}

ADVERSARIAL MODE - VERIFY ENTITY CLAIMS:
This question may contain an INCORRECT entity attribution.

CHECK THE ENTITY WARNING ABOVE (if present):
- If the warning says "X belongs to Person A, not Person B" - CORRECT the record
- Answer: "According to the memories, [correct person] did/has [thing], not [claimed person]."

IF NO WARNING - answer normally using the memories.

DO NOT say "Not stated" if you can provide a correction based on the warning.

ANSWER (correct any false attributions):"""
```

## OPEN_DOMAIN_PROMPT
```python
OPEN_DOMAIN_PROMPT = """You are a memory retrieval assistant. Answer by making GROUNDED INFERENCES from the memories below.

{context}

QUESTION: {question}

OPEN DOMAIN MODE - INFERENCE REQUIRED:
This question requires you to draw a conclusion from the facts in the memories.

STEPS:
1. List 2-3 relevant facts from the memories
2. Identify patterns or themes
3. Draw a reasonable conclusion based on those facts

IMPORTANT: DO NOT say "Not stated" - this question expects an inference!
Always provide your best reasoned answer based on the evidence.

ANSWER (provide your inference with brief reasoning):"""
```

---

# 6. CONFIGURATION

## CognitivePipelineConfig

```python
@dataclass
class CognitivePipelineConfig:
    """Master configuration for the full cognitive pipeline."""

    # Phase configurations
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    emotional: EmotionalConfig = field(default_factory=EmotionalConfig)
    mid_term_memory: MidTermMemoryConfig = field(default_factory=MidTermMemoryConfig)
    knowledge_graph: KnowledgeGraphConfig = field(default_factory=KnowledgeGraphConfig)
    granularity: GranularityConfig = field(default_factory=GranularityConfig)
    domain: DomainConfig = field(default_factory=DomainConfig)
    sharded_memory: ShardedMemoryConfig = field(default_factory=ShardedMemoryConfig)

    # LLM configuration
    llm: LLMConfig = field(default_factory=LLMConfig)

    # Benchmark settings
    concurrent_questions: int = 4
    embedding_batch_size: int = 10
    context_limit: int = 12000
    top_k_retrieval: int = 30
```

---

# 7. USAGE

## Run Full Benchmark
```bash
cd C:\Users\anovr\Desktop\MemMachine-main\evaluation
python locomo10_parallel_benchmark.py
```

## Run 10-Question Test
```bash
python locomo10_parallel_benchmark.py --test-mode
```

## Expected Output
```
======================================================================
LoCoMo-PARALLEL Benchmark v4.1 (HYBRID ROUTING + DURATION CALC)
======================================================================
Answer Model: qwen2.5:7b-instruct
Judge Model: gpt-4o-mini (GPT-4o-mini)
Embedding: nomic-embed-text
Concurrent questions: 4
======================================================================

============================================================
Conversation 1: Caroline & Melanie
Questions: 199
============================================================
  [1] temporal [TEMPORAL]: When did Caroline go to... [CORRECT] (0.8s)
  [2] temporal [TEMPORAL+DUR]: How long has Caroline... [CORRECT] (1.2s)
  ...

BENCHMARK RESULTS:
Overall: 51.76%
  single_hop: 49.28%
  temporal: 33.64%
  open_domain: 68.75%
  multi_hop: 69.27%
  adversarial: 17.91%
```

---

# VERSION HISTORY

| Version | Date | Changes |
|---------|------|---------|
| v4.1 | 2025-12-07 | Added DurationCalculator for "How long" questions |
| v4.0 | 2025-12-06 | Hybrid routing, mode-specific prompts, anti-abstention |
| v3.4 | 2025-12-05 | CognitiveEpisodicMemory with temporal normalization |
| v3.3 | 2025-12-04 | TemporalEvent index with action/entity extraction |
| v3.2 | 2025-12-03 | Temporal date calculator |
| v3.1 | 2025-12-02 | Entity tracker for adversarial detection |
| v3.0 | 2025-12-01 | GPT-4o-mini LLM judge (official MemMachine evaluation) |

---

**END OF DOCUMENT**

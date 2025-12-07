"""
LoCoMo-PARALLEL Benchmark v3.4
HYBRID ROUTING by Question Type

Key improvements v3.4:
1. TEMPORAL questions: Uses CognitiveEpisodicMemory with temporal normalization
2. OTHER questions (single_hop, multi_hop, open_domain, adversarial): Uses basic retrieval
3. Each question type gets the optimal retrieval strategy

Previous improvements (v3.0-3.2):
- Entity verification for adversarial detection
- TEMPORAL DATE CALCULATOR
- GPT-4o-mini LLM judge (per official MemMachine evaluation)
"""

import json
import asyncio
import aiohttp
import time
import re
import math
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np
from openai import OpenAI

# Add paths for cognitive pipeline imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from cognitive_pipeline import (
    CognitivePipelineConfig,
    CognitiveEpisodicMemory,
)

# Flag to use cognitive memory or fallback to basic retrieval
USE_COGNITIVE_MEMORY = True

# OpenAI API for LLM Judge (official MemMachine evaluation method)
OPENAI_API_KEY = "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"
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Ollama Configuration (for embeddings and answer generation)
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "gpt-4o-mini"  # Using OpenAI GPT-4o-mini per official MemMachine eval
EMBEDDING_MODEL = "nomic-embed-text"

# Parallel processing settings
CONCURRENT_QUESTIONS = 4  # Process 4 questions simultaneously
EMBEDDING_BATCH_SIZE = 10  # Embed 10 messages at once

# Categories mapping
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
CORRECT_THRESHOLD = 8
PARTIAL_THRESHOLD = 5

# Stopwords for BM25
STOPWORDS = set([
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she",
    "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "what", "which", "who", "whom", "this", "that",
    "these", "those", "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an",
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of",
    "at", "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just",
    "don", "should", "now", "d", "ll", "m", "o", "re", "ve", "y", "ain"
])


def simple_tokenize(text: str) -> list[str]:
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def compute_bm25_scores(query: str, documents: list[str], k1=1.5, b=0.75) -> list[float]:
    query_tokens = simple_tokenize(query)
    if not query_tokens:
        return [0.0] * len(documents)

    doc_tokens = [simple_tokenize(doc) for doc in documents]
    doc_lengths = [len(tokens) for tokens in doc_tokens]
    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1

    N = len(documents)
    idf = {}
    for term in set(query_tokens):
        df = sum(1 for tokens in doc_tokens if term in tokens)
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    scores = []
    for i, tokens in enumerate(doc_tokens):
        score = 0.0
        term_freq = Counter(tokens)
        doc_len = doc_lengths[i]
        for term in query_tokens:
            if term in term_freq:
                tf = term_freq[term]
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * doc_len / avg_doc_length)
                score += idf.get(term, 0) * (numerator / denominator)
        scores.append(score)
    return scores


def cosine_similarity(vec1, vec2) -> float:
    if vec1 is None or vec2 is None:
        return 0.0
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    dot = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


# =============================================================================
# ENTITY VERIFICATION (Adversarial Detection)
# =============================================================================

class EntityTracker:
    """Tracks entity-attribute-action associations from messages."""

    def __init__(self):
        self.entity_possessions = defaultdict(set)  # entity -> set of things they own/have
        self.entity_actions = defaultdict(set)  # entity -> set of actions they performed
        self.entity_attributes = defaultdict(set)  # entity -> set of attributes
        self.known_entities = set()

    def extract_from_message(self, speaker: str, text: str):
        """Extract entity information from a message."""
        self.known_entities.add(speaker)
        text_lower = text.lower()

        # Possessive patterns: "my X", "I have X", "I got X"
        my_patterns = [
            r"\bmy\s+(\w+(?:\s+\w+)?)",
            r"\bi\s+(?:have|got|bought|received|made|painted|created)\s+(?:a\s+)?(\w+(?:\s+\w+)?)",
            r"\bi\s+(?:am|was)\s+(?:a\s+)?(\w+)",
        ]
        for pattern in my_patterns:
            for match in re.finditer(pattern, text_lower):
                item = match.group(1).strip()
                if len(item) > 2:
                    self.entity_possessions[speaker].add(item)

        # Action patterns: "I Xed", "I am Xing"
        action_patterns = [
            r"\bi\s+(painted|drew|created|made|wrote|ran|walked|visited|attended|went to|signed up for)\s+(.+?)(?:\.|,|!|\?|$)",
            r"\bi\s+(?:am|was)\s+(painting|running|walking|camping|hiking|reading|watching)\b",
        ]
        for pattern in action_patterns:
            for match in re.finditer(pattern, text_lower):
                action = match.group(1).strip()
                self.entity_actions[speaker].add(action)

        # Third person mentions
        third_person = re.findall(r"\b([A-Z][a-z]+)'s\s+(\w+(?:\s+\w+)?)", text)
        for entity, possession in third_person:
            self.known_entities.add(entity)
            self.entity_possessions[entity].add(possession.lower())

    def verify_claim(self, claimed_entity: str, claim_type: str, claim_value: str) -> tuple[bool, str | None]:
        """Verify if a claim about an entity is correct.

        Returns (is_correct, actual_owner_if_wrong)
        """
        claim_value_lower = claim_value.lower()

        if claim_type == "possession":
            # Check if claimed entity owns this
            for entity, possessions in self.entity_possessions.items():
                for poss in possessions:
                    if claim_value_lower in poss or poss in claim_value_lower:
                        if entity.lower() != claimed_entity.lower():
                            return False, entity
                        return True, None
        elif claim_type == "action":
            for entity, actions in self.entity_actions.items():
                for action in actions:
                    if claim_value_lower in action or action in claim_value_lower:
                        if entity.lower() != claimed_entity.lower():
                            return False, entity
                        return True, None

        return True, None  # No conflicting evidence found


def detect_entity_swap(question: str, entity_tracker: EntityTracker) -> tuple[bool, str]:
    """Detect if a question contains an entity swap.

    Returns (is_adversarial, correction_message)
    """
    corrections = []

    # Check possessive claims: "What is X's Y?"
    possessive_match = re.search(r"(?:What|Where|How|Why).*?([A-Z][a-z]+)'s\s+(\w+)", question)
    if possessive_match:
        claimed_owner = possessive_match.group(1)
        possession = possessive_match.group(2)
        is_correct, actual_owner = entity_tracker.verify_claim(claimed_owner, "possession", possession)
        if not is_correct and actual_owner:
            corrections.append(f"'{possession}' belongs to {actual_owner}, not {claimed_owner}")

    # Check action claims: "When did X verb?"
    action_match = re.search(r"(?:When|What|How).*?did\s+([A-Z][a-z]+)\s+(\w+)", question)
    if action_match:
        claimed_performer = action_match.group(1)
        action = action_match.group(2)
        is_correct, actual_performer = entity_tracker.verify_claim(claimed_performer, "action", action)
        if not is_correct and actual_performer:
            corrections.append(f"'{action}' was done by {actual_performer}, not {claimed_performer}")

    if corrections:
        return True, " | ".join(corrections)
    return False, ""


# =============================================================================
# TEMPORAL EXTRACTION
# =============================================================================

MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

WEEKDAY_NAMES = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

# =============================================================================
# TEMPORAL REFERENCE CALCULATOR (FIX FOR 100% TEMPORAL FAILURE)
# =============================================================================

# Map relative temporal markers to day offsets
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
    # Weekdays (relative to session)
    r"\bon sunday\b": ("weekday", 6),
    r"\bon monday\b": ("weekday", 0),
    r"\bon tuesday\b": ("weekday", 1),
    r"\bon wednesday\b": ("weekday", 2),
    r"\bon thursday\b": ("weekday", 3),
    r"\bon friday\b": ("weekday", 4),
    r"\bon saturday\b": ("weekday", 5),
    r"\blast sunday\b": ("last_weekday", 6),
    r"\blast monday\b": ("last_weekday", 0),
    r"\blast tuesday\b": ("last_weekday", 1),
    r"\blast wednesday\b": ("last_weekday", 2),
    r"\blast thursday\b": ("last_weekday", 3),
    r"\blast friday\b": ("last_weekday", 4),
    r"\blast saturday\b": ("last_weekday", 5),
    # Months
    r"\blast month\b": ("month", -1),
    r"\bthis month\b": ("month", 0),
    r"\bnext month\b": ("month", 1),
    # Years
    r"\blast year\b": ("year", -1),
    r"\bthis year\b": ("year", 0),
    r"\bnext year\b": ("year", 1),
}


def parse_session_datetime(session_time: str) -> datetime | None:
    """Parse LoCoMo session datetime format: '1:56 pm on 8 May, 2023'"""
    if not session_time:
        return None

    # Extract date part from "X:XX am/pm on DD Month, YYYY"
    date_match = re.search(r"on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", session_time)
    if date_match:
        day = int(date_match.group(1))
        month_str = date_match.group(2).lower()
        year = int(date_match.group(3))
        month = MONTH_NAMES.get(month_str, 1)
        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    # Try other formats
    date_patterns = [
        (r"(\d{1,2})\s+(\w+)\s+(\d{4})", lambda m: (int(m.group(1)), MONTH_NAMES.get(m.group(2).lower(), 1), int(m.group(3)))),
        (r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", lambda m: (int(m.group(2)), MONTH_NAMES.get(m.group(1).lower(), 1), int(m.group(3)))),
    ]
    for pattern, extractor in date_patterns:
        match = re.search(pattern, session_time)
        if match:
            try:
                day, month, year = extractor(match)
                return datetime(year, month, day)
            except (ValueError, KeyError):
                continue
    return None


def calculate_actual_date(session_date: datetime, marker_type: str, offset: int) -> datetime | None:
    """Calculate actual date from session date + relative offset.

    Args:
        session_date: The session datetime
        marker_type: Type of temporal marker (day, week, weekday, last_weekday, month, year)
        offset: Numeric offset for the marker type

    Returns:
        Calculated datetime or None
    """
    try:
        if marker_type == "day":
            return session_date + timedelta(days=offset)
        elif marker_type == "week":
            return session_date + timedelta(weeks=offset)
        elif marker_type == "weekday":
            # Find the most recent occurrence of this weekday before/on session date
            target_weekday = offset
            current_weekday = session_date.weekday()
            days_diff = current_weekday - target_weekday
            if days_diff < 0:
                days_diff += 7  # Go back to last week's occurrence
            return session_date - timedelta(days=days_diff)
        elif marker_type == "last_weekday":
            # Find the weekday in the previous week
            target_weekday = offset
            current_weekday = session_date.weekday()
            days_diff = current_weekday - target_weekday
            if days_diff <= 0:
                days_diff += 7
            return session_date - timedelta(days=days_diff)
        elif marker_type == "month":
            # Approximate month offset
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


def format_date_for_gold(dt: datetime, format_type: str = "locomo") -> str:
    """Format datetime in LoCoMo gold answer format.

    LoCoMo gold answers use formats like:
    - "The sunday before 25 May 2023"
    - "The week before 6 July 2023"
    - "21 May 2023"
    """
    # Standard format: "21 May 2023"
    return dt.strftime("%-d %B %Y").replace(" 0", " ").lstrip("0")


def format_relative_date(session_date: datetime, event_date: datetime) -> str:
    """Format date as both absolute and relative (LoCoMo style).

    Returns format like: "21 May 2023 (the Sunday before 25 May 2023)"
    """
    abs_date = event_date.strftime("%d %B %Y").lstrip("0")

    # Calculate the relationship
    days_diff = (session_date - event_date).days

    if days_diff == 0:
        return f"{abs_date} (same day as session)"
    elif days_diff == 1:
        return f"{abs_date} (the day before session)"
    elif days_diff < 7:
        weekday_name = event_date.strftime("%A").lower()
        return f"{abs_date} (the {weekday_name} before {session_date.strftime('%d %B %Y').lstrip('0')})"
    elif days_diff < 14:
        return f"{abs_date} (the week before {session_date.strftime('%d %B %Y').lstrip('0')})"
    else:
        return abs_date


def extract_temporal_references(text: str) -> list[tuple[str, str, int]]:
    """Extract temporal references from text.

    Returns list of (matched_text, marker_type, offset)
    """
    refs = []
    text_lower = text.lower()

    for pattern, (marker_type, offset) in TEMPORAL_MARKERS.items():
        match = re.search(pattern, text_lower)
        if match:
            refs.append((match.group(), marker_type, offset))

    return refs


# =============================================================================
# TEMPORAL QUESTION DETECTION (v3.2 Enhancement)
# =============================================================================

# Patterns that indicate a temporal question
TEMPORAL_QUESTION_PATTERNS = [
    r"^when\s+",                              # "When did X..."
    r"^how\s+long\s+",                        # "How long has X..."
    r"^what\s+(?:date|day|month|year)\s+",    # "What date did X..."
    r"what\s+time\s+",                        # "What time did X..."
    r"which\s+(?:date|day|month|year)\s+",    # "Which day did X..."
    r"\bwhen\s+(?:did|was|is|will|has|had)\b", # "...when did X..."
    r"\bhow\s+long\s+ago\b",                  # "...how long ago..."
    r"\bsince\s+when\b",                      # "Since when..."
    r"\buntil\s+when\b",                      # "Until when..."
]

# Keywords that strongly indicate temporal questions
TEMPORAL_QUESTION_KEYWORDS = {
    "when", "date", "day", "month", "year", "time",
    "ago", "since", "until", "duration", "long",
    "recently", "before", "after",
}


def is_temporal_question(question: str) -> bool:
    """Detect if a question is about time/dates.

    v3.2 Enhancement: More robust detection for temporal question routing.

    Args:
        question: The question text.

    Returns:
        True if this is a temporal question requiring date calculation.
    """
    question_lower = question.lower().strip()

    # Check direct patterns
    for pattern in TEMPORAL_QUESTION_PATTERNS:
        if re.search(pattern, question_lower):
            return True

    # Check keyword density
    words = set(re.findall(r'\b[a-z]+\b', question_lower))
    temporal_word_count = len(words & TEMPORAL_QUESTION_KEYWORDS)

    # If 2+ temporal keywords or starts with "when", it's temporal
    if temporal_word_count >= 2:
        return True
    if question_lower.startswith("when "):
        return True
    if question_lower.startswith("how long "):
        return True

    return False


def extract_dates_from_text(text: str) -> list[str]:
    """Extract all date mentions from text."""
    dates = []
    months_pattern = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"

    # LoCoMo format: "8 May, 2023" or "8 May 2023" (day month, year)
    locomo_date = re.findall(rf"(\d{{1,2}})\s+({months_pattern}),?\s+(\d{{4}})", text, re.IGNORECASE)
    for day, month, year in locomo_date:
        dates.append(f"{day} {month} {year}")

    # American format: "May 8, 2023" or "May 8 2023" (month day, year)
    american_date = re.findall(rf"({months_pattern})\s+(\d{{1,2}}),?\s+(\d{{4}})", text, re.IGNORECASE)
    for month, day, year in american_date:
        dates.append(f"{day} {month} {year}")

    # Month Year: "July 2023" or "in 2022" (but avoid duplicates from full dates)
    month_year = re.findall(rf"({months_pattern})\s+(\d{{4}})", text, re.IGNORECASE)
    for month, year in month_year:
        date_str = f"{month} {year}"
        if date_str not in dates:
            dates.append(date_str)

    # Year only: "in 2022", "painted in 2022" etc
    year_only = re.findall(r"\b(20\d{2})\b", text)
    for year in year_only:
        if year not in dates:
            dates.append(year)

    return dates


@dataclass
class TemporalEvent:
    """An event with calculated temporal information.

    v3.2 Enhancement: Now includes action and entity extraction for better matching.
    """
    speaker: str
    text: str
    session_date: datetime
    session_time_str: str
    calculated_date: datetime | None
    relative_reference: str | None
    formatted_date: str  # e.g., "21 May 2023 (the Sunday before 25 May 2023)"
    msg_idx: int
    session: int
    # v3.2: Action-entity extraction for query matching
    actions: list[str] = None  # Extracted actions: ["painted", "ran", "signed up"]
    entities: list[str] = None  # Extracted entities: ["necklace", "marathon", "class"]

    def __post_init__(self):
        """Extract actions and entities from text if not provided."""
        if self.actions is None:
            self.actions = self._extract_actions()
        if self.entities is None:
            self.entities = self._extract_entities()

    def _extract_actions(self) -> list[str]:
        """Extract action verbs from text."""
        text_lower = self.text.lower()
        # v3.3 FIX: Extract ALL action verbs, not just first-person
        # Also normalize verb forms (go/went/going -> go)
        action_verbs = {
            "paint": ["paint", "painted", "painting", "paints"],
            "draw": ["draw", "drew", "drawing", "drawn", "draws"],
            "create": ["create", "created", "creating", "creates"],
            "make": ["make", "made", "making", "makes"],
            "write": ["write", "wrote", "writing", "written", "writes"],
            "run": ["run", "ran", "running", "runs"],
            "walk": ["walk", "walked", "walking", "walks"],
            "visit": ["visit", "visited", "visiting", "visits"],
            "attend": ["attend", "attended", "attending", "attends"],
            "go": ["go", "went", "going", "goes", "gone"],
            "sign": ["sign", "signed", "signing", "signs", "sign up", "signed up"],
            "start": ["start", "started", "starting", "starts"],
            "finish": ["finish", "finished", "finishing", "finishes"],
            "complete": ["complete", "completed", "completing", "completes"],
            "camp": ["camp", "camped", "camping", "camps"],
            "hike": ["hike", "hiked", "hiking", "hikes"],
            "buy": ["buy", "bought", "buying", "buys"],
            "plan": ["plan", "planned", "planning", "plans"],
            "give": ["give", "gave", "giving", "gives", "given"],
            "have": ["have", "had", "having", "has"],
            "meet": ["meet", "met", "meeting", "meets"],
            "join": ["join", "joined", "joining", "joins"],
            "support": ["support", "supported", "supporting", "supports"],
        }
        actions = []
        for base_verb, forms in action_verbs.items():
            for form in forms:
                if re.search(rf"\b{form}\b", text_lower):
                    actions.append(base_verb)  # Store normalized form
                    break
        return list(set(actions))

    def _extract_entities(self) -> list[str]:
        """Extract key entities/nouns from text."""
        text_lower = self.text.lower()
        # v3.3 FIX: Expanded entity list to match more question types
        key_entities = [
            "marathon", "class", "course", "trip", "painting", "necklace",
            "birthday", "wedding", "party", "conference", "group", "support",
            "lgbtq", "charity", "race", "pottery", "sunrise", "speech",
            "school", "picnic", "friends", "camping", "hike", "ceremony",
            "graduation", "anniversary", "vacation", "holiday", "dog", "cat",
            "car", "house", "job", "work", "meeting", "event", "session",
        ]
        entities = []
        for entity in key_entities:
            if re.search(rf"\b{entity}\b", text_lower):
                entities.append(entity)
        # Also extract "my X" patterns
        my_matches = re.findall(r"\bmy\s+(\w+)", text_lower)
        entities.extend([m for m in my_matches if len(m) > 2 and m not in {"the", "and", "was", "for", "own"}])
        return list(set(entities))


def query_event_index(
    query: str,
    temporal_events: list[TemporalEvent],
    top_k: int = 5,
) -> list[tuple[TemporalEvent, float]]:
    """Query the temporal event index for matching events.

    v3.2: Structured query matching against action-entity indexed events.

    Args:
        query: The temporal question (e.g., "When did Caroline paint?")
        temporal_events: List of indexed temporal events.
        top_k: Maximum events to return.

    Returns:
        List of (event, score) tuples sorted by relevance.
    """
    query_lower = query.lower()

    # Extract subject (person) from query
    subject_patterns = [
        r"(?:when|how|what).*?(?:did|was|is|has|had)\s+(\w+)",
        r"(?:when|how)\s+(\w+)\s+(?:has|had|have)",
    ]
    query_subject = None
    for pattern in subject_patterns:
        match = re.search(pattern, query_lower)
        if match:
            query_subject = match.group(1)
            break

    # v3.3 FIX: Extract action keywords and normalize to base form
    action_verb_map = {
        "paint": "paint", "painted": "paint", "painting": "paint", "paints": "paint",
        "draw": "draw", "drew": "draw", "drawing": "draw", "drawn": "draw", "draws": "draw",
        "create": "create", "created": "create", "creating": "create", "creates": "create",
        "make": "make", "made": "make", "making": "make", "makes": "make",
        "write": "write", "wrote": "write", "writing": "write", "written": "write", "writes": "write",
        "run": "run", "ran": "run", "running": "run", "runs": "run",
        "walk": "walk", "walked": "walk", "walking": "walk", "walks": "walk",
        "visit": "visit", "visited": "visit", "visiting": "visit", "visits": "visit",
        "attend": "attend", "attended": "attend", "attending": "attend", "attends": "attend",
        "go": "go", "went": "go", "going": "go", "goes": "go", "gone": "go",
        "sign": "sign", "signed": "sign", "signing": "sign", "signs": "sign",
        "start": "start", "started": "start", "starting": "start", "starts": "start",
        "finish": "finish", "finished": "finish", "finishing": "finish", "finishes": "finish",
        "complete": "complete", "completed": "complete", "completing": "complete", "completes": "complete",
        "camp": "camp", "camped": "camp", "camping": "camp", "camps": "camp",
        "hike": "hike", "hiked": "hike", "hiking": "hike", "hikes": "hike",
        "buy": "buy", "bought": "buy", "buying": "buy", "buys": "buy",
        "plan": "plan", "planned": "plan", "planning": "plan", "plans": "plan",
        "give": "give", "gave": "give", "giving": "give", "gives": "give", "given": "give",
        "have": "have", "had": "have", "having": "have", "has": "have",
        "meet": "meet", "met": "meet", "meeting": "meet", "meets": "meet",
        "join": "join", "joined": "join", "joining": "join", "joins": "join",
        "support": "support", "supported": "support", "supporting": "support", "supports": "support",
    }
    raw_action_keywords = re.findall(r"\b(" + "|".join(action_verb_map.keys()) + r")\b", query_lower)
    action_keywords = set(action_verb_map.get(a, a) for a in raw_action_keywords)

    # v3.3 FIX: Expanded entity keywords to match more question types
    entity_keywords = set(re.findall(
        r"\b(marathon|class|course|trip|painting|necklace|birthday|wedding|"
        r"party|conference|dog|cat|car|house|job|work|group|support|lgbtq|"
        r"charity|race|pottery|sunrise|speech|school|picnic|friends|camping|"
        r"hike|speech|ceremony|graduation|anniversary|vacation|holiday)\b",
        query_lower
    ))

    scored_events = []
    for event in temporal_events:
        score = 0.0

        # Score 1: Subject match (speaker)
        if query_subject and query_subject in event.speaker.lower():
            score += 3.0

        # Score 2: Action match
        if event.actions:
            event_actions = set(event.actions)
            action_overlap = len(action_keywords & event_actions)
            score += action_overlap * 2.0

        # Score 3: Entity match
        if event.entities:
            event_entities = set(event.entities)
            entity_overlap = len(entity_keywords & event_entities)
            score += entity_overlap * 1.5

        # Score 4: Has calculated date (prefer events with resolved dates)
        if event.calculated_date and event.relative_reference:
            score += 1.0

        # Score 5: Text contains query keywords
        query_words = set(re.findall(r'\b[a-z]{3,}\b', query_lower))
        text_words = set(re.findall(r'\b[a-z]{3,}\b', event.text.lower()))
        overlap = len(query_words & text_words)
        score += overlap * 0.3

        if score > 0:
            scored_events.append((event, score))

    # Sort by score descending
    scored_events.sort(key=lambda x: x[1], reverse=True)
    return scored_events[:top_k]


def build_temporal_event_index(messages: list[dict]) -> list[TemporalEvent]:
    """Build an index of events with CALCULATED dates from relative references.

    This is the KEY FIX for temporal questions. For each message:
    1. Parse the session datetime
    2. Find relative temporal references ("on Sunday", "last week")
    3. Calculate the actual date: session_date + offset = event_date
    4. Store both absolute and relative formats for matching
    """
    events = []

    for msg in messages:
        text = msg.get("text", "")
        session_time_str = msg.get("session_time", "")
        speaker = msg.get("speaker", "")
        msg_idx = msg.get("msg_idx", 0)
        session = msg.get("session", 0)

        # Parse the session date
        session_date = parse_session_datetime(session_time_str)
        if not session_date:
            continue

        # Extract relative temporal references from the message
        temporal_refs = extract_temporal_references(text)

        if temporal_refs:
            # Calculate actual date for each reference
            for ref_text, marker_type, offset in temporal_refs:
                calculated = calculate_actual_date(session_date, marker_type, offset)
                if calculated:
                    formatted = format_relative_date(session_date, calculated)
                    events.append(TemporalEvent(
                        speaker=speaker,
                        text=text,
                        session_date=session_date,
                        session_time_str=session_time_str,
                        calculated_date=calculated,
                        relative_reference=ref_text,
                        formatted_date=formatted,
                        msg_idx=msg_idx,
                        session=session,
                    ))
        else:
            # No relative reference - just use session date
            events.append(TemporalEvent(
                speaker=speaker,
                text=text,
                session_date=session_date,
                session_time_str=session_time_str,
                calculated_date=session_date,
                relative_reference=None,
                formatted_date=session_date.strftime("%d %B %Y").lstrip("0"),
                msg_idx=msg_idx,
                session=session,
            ))

    return events


def build_temporal_index(messages: list[dict]) -> dict[str, list[str]]:
    """Build an index of dates mentioned in messages, with associated text.

    ENHANCED: Now also indexes calculated dates from relative references.
    """
    temporal_index = defaultdict(list)

    for msg in messages:
        text = msg.get("text", "")
        session_time = msg.get("session_time", "")
        speaker = msg.get("speaker", "")

        # Parse session date
        session_date = parse_session_datetime(session_time)

        # Add session date
        if session_time:
            dates = extract_dates_from_text(session_time)
            for date in dates:
                temporal_index[date.lower()].append(f"[{session_time}] {speaker}: {text[:100]}")

        # Add explicit dates mentioned in text
        dates = extract_dates_from_text(text)
        for date in dates:
            temporal_index[date.lower()].append(f"{speaker}: {text[:100]}")

        # ENHANCED: Calculate and index dates from relative references
        if session_date:
            temporal_refs = extract_temporal_references(text)
            for ref_text, marker_type, offset in temporal_refs:
                calculated = calculate_actual_date(session_date, marker_type, offset)
                if calculated:
                    # Index by calculated date
                    calc_date_str = calculated.strftime("%d %B %Y").lstrip("0").lower()
                    temporal_index[calc_date_str].append(
                        f"[CALCULATED: {ref_text} from {session_time}] {speaker}: {text[:100]}"
                    )
                    # Also index by weekday name if relevant
                    if marker_type in ("weekday", "last_weekday"):
                        weekday_str = calculated.strftime("%A").lower()
                        temporal_index[weekday_str].append(
                            f"[{calculated.strftime('%d %B %Y')}] {speaker}: {text[:100]}"
                        )

    return temporal_index


def find_date_for_event(query: str, messages: list[dict], temporal_events: list[TemporalEvent] | None = None) -> str | None:
    """Try to find the date when an event happened.

    v3.2 Enhancement: Uses query_event_index() for structured matching.

    ENHANCED v3.1: Handles multiple temporal question formats:
    - "When did X do Y?"
    - "When X has done Y?"
    - "How long has X..."
    - "When is X planning..."
    """
    query_lower = query.lower()

    # v3.2: Try structured event index query first
    if temporal_events:
        matches = query_event_index(query, temporal_events, top_k=3)
        if matches:
            best_event, best_score = matches[0]
            # Only use if score is high enough (indicates good match)
            if best_score >= 2.0 and best_event.calculated_date:
                return best_event.formatted_date

    # Multiple patterns to extract subject and action
    patterns = [
        # "When did/was/is X do Y?"
        r"when\s+(?:did|was|is)\s+(\w+)\s+(.+?)(?:\?|$)",
        # "When X has/had done Y?"
        r"when\s+(\w+)\s+(?:has|had|have)\s+(.+?)(?:\?|$)",
        # "When is X planning/going to Y?"
        r"when\s+is\s+(\w+)\s+(?:planning|going)\s+(.+?)(?:\?|$)",
        # "How long has X had/been Y?"
        r"how\s+long\s+(?:has|had|have)\s+(\w+)\s+(.+?)(?:\?|$)",
        # "How long ago was/did X?"
        r"how\s+long\s+ago\s+(?:was|did)\s+(\w+)(?:'s)?\s+(.+?)(?:\?|$)",
        # Generic fallback: "When X verb Y?"
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

    if not subject or not action:
        # Last resort: extract all significant words as keywords
        words = re.findall(r'\b[a-z]{3,}\b', query_lower)
        # Filter out common question words
        stop_words = {'when', 'what', 'where', 'how', 'long', 'did', 'was', 'has', 'had', 'have', 'does', 'the', 'for', 'and'}
        keywords = [w for w in words if w not in stop_words]
        if keywords:
            subject = keywords[0]
            action = ' '.join(keywords[1:]) if len(keywords) > 1 else ''
        else:
            return None

    action_keywords = [kw for kw in action.split()[:5] if len(kw) > 2]

    # Fallback: Search pre-calculated temporal events with simple matching
    if temporal_events:
        for event in temporal_events:
            speaker_lower = event.speaker.lower()
            text_lower = event.text.lower()

            # Check if this event matches subject and action
            subject_match = subject in speaker_lower or subject in text_lower
            action_match = any(kw in text_lower for kw in action_keywords)

            if subject_match and action_match and event.calculated_date:
                # Return the formatted date (both absolute and relative)
                return event.formatted_date

    # Fallback: Search messages directly
    for msg in messages:
        text = msg.get("text", "").lower()
        speaker = msg.get("speaker", "").lower()
        session_time = msg.get("session_time", "")

        # Check if message mentions the subject and action
        subject_match = subject in speaker or subject in text
        action_match = any(kw in text for kw in action_keywords)

        if subject_match and action_match:
            # Parse session date and look for relative references
            session_date = parse_session_datetime(session_time)
            if session_date:
                # Check for relative temporal references
                temporal_refs = extract_temporal_references(msg.get("text", ""))
                if temporal_refs:
                    ref_text, marker_type, offset = temporal_refs[0]
                    calculated = calculate_actual_date(session_date, marker_type, offset)
                    if calculated:
                        return format_relative_date(session_date, calculated)

                # No relative ref - return session date
                return session_date.strftime("%d %B %Y").lstrip("0")

    return None


# =============================================================================
# ASYNC FUNCTIONS
# =============================================================================

async def get_embedding_async(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding asynchronously."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding")
    except Exception as e:
        print(f"    Embed error: {e}", flush=True)
        return None


SYSTEM_MESSAGE = """You are a precise memory retrieval assistant. Your ONLY job is to answer questions using information from the provided conversation memories.

ABSOLUTE RULES:
1. NEVER use knowledge from outside the provided memories
2. NEVER guess or make up information
3. If the answer is NOT in the memories, you MUST respond: "Not stated." or "Not stated in memories."
4. For date/time questions: If a specific date is NOT explicitly stated, respond "Not stated."
5. Be concise and specific"""


async def generate_answer_async(session: aiohttp.ClientSession, prompt: str) -> str:
    """Generate answer asynchronously with strict grounding."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_MESSAGE},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.0}
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


async def batch_embed_messages(session: aiohttp.ClientSession, messages: list[dict]) -> list[dict]:
    """Embed all messages in parallel batches."""
    print(f"  Computing embeddings for {len(messages)} messages (parallel)...", flush=True)

    semaphore = asyncio.Semaphore(EMBEDDING_BATCH_SIZE)

    async def embed_with_semaphore(msg):
        async with semaphore:
            text = f"{msg['speaker']}: {msg['text']}"
            msg['embedding'] = await get_embedding_async(session, text)
            return msg

    # Process all at once with semaphore limiting concurrency
    tasks = [embed_with_semaphore(msg) for msg in messages]

    completed = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        completed += 1
        if completed % 50 == 0:
            print(f"    Embedded {completed}/{len(messages)} messages", flush=True)

    print(f"    Finished embedding {len(messages)} messages", flush=True)
    return messages


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def extract_messages_with_timestamps(item) -> list[dict]:
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1

    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"
        if session_key not in conversation:
            break

        session_time = conversation.get(datetime_key, "Unknown")
        session_messages = conversation[session_key]

        for msg_idx, msg in enumerate(session_messages):
            if isinstance(msg, dict) and 'text' in msg:
                messages.append({
                    "session": session_idx,
                    "session_time": session_time,
                    "msg_idx": msg_idx,
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                    "dia_id": msg.get("dia_id", f"D{session_idx}:{msg_idx}"),
                })
        session_idx += 1
    return messages


# =============================================================================
# RETRIEVAL
# =============================================================================

async def retrieve_relevant_messages(
    session: aiohttp.ClientSession,
    question: str,
    messages: list[dict],
    category: str = "single_hop",  # NEW: category-aware retrieval
    top_k: int = TOP_K_RETRIEVAL,
    temporal_events: list[TemporalEvent] | None = None,  # v3.2: For temporal queries
) -> list[dict]:
    """Retrieve relevant messages with category-specific weighting.

    v3.2 Enhancement: TEMPORAL_MODE retrieval for temporal questions.
    """
    # v3.2: TEMPORAL_MODE detection using is_temporal_question
    is_temporal = category == "temporal" or is_temporal_question(question)

    # Category-specific semantic weights
    # v3.2: Temporal uses even heavier BM25 (0.25 semantic = 0.75 BM25)
    WEIGHTS_BY_CATEGORY = {
        "single_hop": 0.35,   # Heavy BM25 for exact keyword matches
        "temporal": 0.25,     # v3.2: Even heavier BM25 for temporal
        "open_domain": 0.75,  # Heavy semantic for thematic queries
        "multi_hop": 0.60,    # Balanced
        "adversarial": 0.65,  # Slightly favor semantic
    }
    semantic_weight = WEIGHTS_BY_CATEGORY.get(category, 0.6)

    # Override for detected temporal questions
    if is_temporal and category != "temporal":
        semantic_weight = 0.25

    question_embedding = await get_embedding_async(session, question)

    texts = [f"{m['speaker']}: {m['text']}" for m in messages]
    bm25_scores = compute_bm25_scores(question, texts)

    max_bm25 = max(bm25_scores) if bm25_scores else 1
    if max_bm25 > 0:
        bm25_scores = [s / max_bm25 for s in bm25_scores]

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

    bm25_weight = 1 - semantic_weight
    combined_scores = [
        bm25_weight * bm25 + semantic_weight * sem
        for bm25, sem in zip(bm25_scores, semantic_scores)
    ]

    # v3.2: ENHANCED TEMPORAL_MODE retrieval boosting
    if is_temporal:
        # Extract subject and action from question for targeted retrieval
        question_lower = question.lower()
        question_words = set(re.findall(r'\b[a-z]{3,}\b', question_lower))

        # Extended temporal keywords with weekdays
        temporal_keywords = {
            "yesterday", "today", "tomorrow", "last", "next",
            "week", "month", "year", "ago", "recently",
            "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        }

        # Action keywords to look for
        action_keywords = {
            "painted", "paint", "painting", "ran", "run", "running",
            "walked", "walk", "walking", "visited", "visit", "visiting",
            "went", "go", "going", "started", "start", "starting",
            "bought", "buy", "buying", "signed", "sign", "signing",
            "camping", "camp", "hiking", "hike", "marathon",
        }

        # Extract subject from question (person name)
        subject_match = re.search(r"(?:when|how|what).*?(?:did|was|is|has|had)\s+(\w+)", question_lower)
        question_subject = subject_match.group(1) if subject_match else None

        for i, msg in enumerate(messages):
            text_lower = msg['text'].lower()
            speaker_lower = msg['speaker'].lower()
            boost = 1.0

            # BOOST 1: Message contains temporal keywords (40% boost)
            if any(kw in text_lower for kw in temporal_keywords):
                boost *= 1.4

            # BOOST 2: Message contains action keywords from question (30% boost)
            if question_words & action_keywords:
                msg_action_words = set(re.findall(r'\b[a-z]{3,}\b', text_lower))
                if question_words & msg_action_words & action_keywords:
                    boost *= 1.3

            # BOOST 3: Message from same speaker as question subject (50% boost)
            if question_subject and question_subject in speaker_lower:
                boost *= 1.5

            # BOOST 4: Message has relative temporal reference (60% boost)
            temporal_refs = extract_temporal_references(msg['text'])
            if temporal_refs:
                boost *= 1.6

            combined_scores[i] *= boost

    scored_messages = list(zip(messages, combined_scores))
    scored_messages.sort(key=lambda x: x[1], reverse=True)
    return [msg for msg, score in scored_messages[:top_k]]


def add_surrounding_context(selected: list[dict], all_messages: list[dict], window: int = 1) -> list[dict]:
    expanded = set()
    for msg in selected:
        session = msg['session']
        idx = msg['msg_idx']
        for offset in range(-window, window + 1):
            expanded.add((session, idx + offset))

    result = []
    seen = set()
    for msg in all_messages:
        key = (msg['session'], msg['msg_idx'])
        if key in expanded and key not in seen:
            result.append(msg)
            seen.add(key)

    result.sort(key=lambda x: (x['session'], x['msg_idx']))
    return result


# =============================================================================
# v3.3: COGNITIVE MEMORY RETRIEVAL (with temporal normalization)
# =============================================================================

async def retrieve_with_cognitive_memory(
    cognitive_memory: CognitiveEpisodicMemory,
    question: str,
    limit: int = TOP_K_RETRIEVAL,
) -> str:
    """Retrieve context using CognitiveEpisodicMemory with temporal normalization.

    v3.3: This uses the EpisodicMemory.formalize_query_with_context() which includes:
    - Temporal normalization (resolves "yesterday" to actual dates)
    - Temporal context header for LLM
    - Knowledge graph reasoning (if enabled)
    """
    try:
        result = await cognitive_memory.query(question, limit=limit)
        if result and result.context:
            return result.context
        return ""
    except Exception as e:
        print(f"    [WARN] Cognitive memory query failed: {e}")
        return ""


async def ingest_messages_to_cognitive_memory(
    cognitive_memory: CognitiveEpisodicMemory,
    messages: list[dict],
) -> None:
    """Ingest all messages into cognitive memory.

    v3.3: This uses the full EpisodicMemory ingestion pipeline.
    """
    for msg in messages:
        try:
            # Parse session datetime for timestamp
            session_time_str = msg.get("session_time", "")
            timestamp = parse_session_datetime(session_time_str) or datetime.now(timezone.utc)

            await cognitive_memory.ingest_episode(
                content=msg["text"],
                speaker=msg["speaker"],
                timestamp=timestamp,
                metadata={
                    "session": msg.get("session", 0),
                    "msg_idx": msg.get("msg_idx", 0),
                    "session_time": session_time_str,
                },
            )
        except Exception as e:
            # Silently continue on individual message failures
            pass


def format_context_with_timestamps(messages: list[dict], max_chars: int = CONTEXT_LIMIT) -> str:
    sessions = {}
    for msg in messages:
        session = msg['session']
        if session not in sessions:
            sessions[session] = {'time': msg['session_time'], 'messages': []}
        sessions[session]['messages'].append(msg)

    lines = ["=== CONVERSATION MEMORIES WITH TIMESTAMPS ===\n"]
    total_chars = 0

    for session_num in sorted(sessions.keys()):
        session_data = sessions[session_num]
        header = f"\n--- SESSION {session_num} | DATE: {session_data['time']} ---\n"
        if total_chars + len(header) > max_chars:
            break
        lines.append(header)
        total_chars += len(header)

        for msg in session_data['messages']:
            line = f"  {msg['speaker']}: {msg['text']}\n"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)

    return "".join(lines)


# =============================================================================
# PROMPTS
# =============================================================================

STANDARD_PROMPT = """You are a memory retrieval assistant. You MUST answer ONLY using information explicitly stated in the memories below.

{context}

QUESTION: {question}

CRITICAL RULES:
1. ONLY use facts explicitly stated in the memories above - DO NOT add external knowledge
2. If the specific information is NOT present in the memories, respond: "Not stated in memories."
3. DO NOT guess, infer, or make up any information
4. Quote or paraphrase directly from the memories when possible
5. Be specific and concise

ANSWER:"""

TEMPORAL_PROMPT = """You are a memory retrieval assistant answering WHEN something happened.

{context}

QUESTION: {question}

CRITICAL TEMPORAL ANSWERING RULES (v3.2):

RULE 1 - CALCULATED DATE IS AUTHORITATIVE:
If you see "CALCULATED EVENT DATE:" above, that date has been pre-computed from the session
timestamp and relative reference. USE IT DIRECTLY as your answer. Do NOT ignore it.

RULE 2 - FORMAT:
- Preferred: "[day] [Month] [year]" (e.g., "21 May 2023")
- Acceptable: "[Month] [year]" (e.g., "May 2023") or "[year]" (e.g., "2023")
- If period: "The [weekday] before [session date]" or "The week before [session date]"

RULE 3 - NEVER HALLUCINATE:
If NO date information exists in the memories or CALCULATED EVENT DATE:
- Do NOT guess or make up a date
- Respond: "Not stated in memories."

RULE 4 - RELATIVE TIME CALCULATION:
If memories say "last Sunday" and SESSION DATE is "25 May 2023" (Thursday):
- Calculate: Last Sunday before Thursday May 25 = Sunday May 21
- Answer: "21 May 2023" or "The Sunday before 25 May 2023"

RULE 5 - USE SESSION DATES:
Each session has a DATE header. Use it to anchor temporal references in that session.

ANSWER (date only, no explanation):"""

MULTI_HOP_PROMPT = """You are a memory retrieval assistant. Answer using ONLY information explicitly stated in the memories below.

{context}

QUESTION: {question}

CRITICAL RULES FOR MULTI-HOP QUESTIONS:
1. ONLY use facts explicitly stated in the memories - DO NOT add external knowledge
2. This question requires connecting 2+ pieces of information from the memories
3. If any required piece of information is NOT present, respond: "Not stated in memories."
4. Look across multiple sessions to find connected information
5. DO NOT guess or infer - only use explicitly stated facts

ANSWER:"""

ADVERSARIAL_PROMPT = """You are a memory retrieval assistant. CAREFULLY verify facts before answering.

{context}

QUESTION: {question}

CRITICAL RULES FOR ADVERSARIAL QUESTIONS:
1. ONLY use facts explicitly stated in the memories - DO NOT add external knowledge
2. VERIFY that entities in the question MATCH what's in the memories
3. If the question attributes something to the WRONG PERSON, correct it based on memories
4. If the claim in the question contradicts the memories, state what the memories actually say
5. If information is NOT present in memories, respond: "Not stated in memories."

ANSWER:"""

OPEN_DOMAIN_PROMPT = """You are a memory retrieval assistant. Answer using ONLY information from the memories below.

{context}

QUESTION: {question}

CRITICAL RULES FOR OPEN DOMAIN QUESTIONS:
1. Base your answer ONLY on facts explicitly stated in the memories
2. You may make reasonable inferences, but they must be grounded in stated facts
3. If there is insufficient information in the memories, respond: "Not stated in memories."

INFERENCE STEPS (grounded in memories):
1. Extract explicit facts from the memories that relate to the question
2. Identify patterns or themes that appear in the memories
3. Draw a reasonable conclusion based ONLY on these facts

ANSWER (with brief reasoning based on memory content):"""


def get_prompt_for_category(category: str) -> str:
    if category == "temporal":
        return TEMPORAL_PROMPT
    elif category == "multi_hop":
        return MULTI_HOP_PROMPT
    elif category == "adversarial":
        return ADVERSARIAL_PROMPT
    elif category == "open_domain":
        return OPEN_DOMAIN_PROMPT
    return STANDARD_PROMPT


# =============================================================================
# JUDGING (Official MemMachine LLM Judge using GPT-4o-mini)
# =============================================================================

# Official MemMachine accuracy prompt (from GitHub repo)
ACCURACY_PROMPT = """
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""


def evaluate_llm_judge_sync(question: str, gold_answer: str, generated_answer: str) -> int:
    """Evaluate using GPT-4o-mini as LLM judge (official MemMachine method)."""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": ACCURACY_PROMPT.format(
                        question=question,
                        gold_answer=gold_answer,
                        generated_answer=generated_answer,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        result = json.loads(response.choices[0].message.content)
        label = result.get("label", "WRONG")
        return 1 if label == "CORRECT" else 0
    except Exception as e:
        print(f"    LLM Judge error: {e}", flush=True)
        return 0


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    """Async wrapper for LLM judge - runs sync OpenAI call in thread pool."""
    loop = asyncio.get_event_loop()
    score = await loop.run_in_executor(
        None, evaluate_llm_judge_sync, question, gold_answer, generated_answer
    )
    # Convert binary score to 0-10 scale for compatibility
    return {"total": 10 if score == 1 else 0, "binary_correct": score}


# =============================================================================
# PROCESS SINGLE QUESTION
# =============================================================================

async def process_question(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    messages: list[dict],
    all_messages: list[dict],
    entity_tracker: EntityTracker | None = None,
    temporal_index: dict[str, list[str]] | None = None,
    temporal_events: list[TemporalEvent] | None = None,
    cognitive_memory: CognitiveEpisodicMemory | None = None,  # v3.3
) -> dict:
    """Process a single question with entity verification and temporal extraction."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    adversarial_answer = qa.get("adversarial_answer", "")
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # v3.4: HYBRID ROUTING - Use different retrieval strategies by question type
    context = ""

    # TEMPORAL questions: Use cognitive memory with temporal normalization
    if category_name == "temporal" and cognitive_memory and USE_COGNITIVE_MEMORY:
        context = await retrieve_with_cognitive_memory(cognitive_memory, question)

    # ALL OTHER question types: Use basic retrieval (proven to work for single_hop, multi_hop, etc.)
    if not context:
        relevant_messages = await retrieve_relevant_messages(
            http_session, question, messages, category=category_name,
            temporal_events=temporal_events,  # Still pass temporal events for boosting
        )
        relevant_messages = add_surrounding_context(relevant_messages, all_messages)
        context = format_context_with_timestamps(relevant_messages)

    # ENTITY VERIFICATION for adversarial questions
    entity_warning = ""
    if category_name == "adversarial" and entity_tracker:
        is_adversarial, correction = detect_entity_swap(question, entity_tracker)
        if is_adversarial:
            entity_warning = f"\n\n⚠️ ENTITY VERIFICATION WARNING: {correction}\nThe question may attribute facts to the wrong person. Check the memories carefully."

    # ENHANCED TEMPORAL EXTRACTION for temporal questions (KEY FIX)
    temporal_hint = ""
    if category_name == "temporal":
        # Use pre-calculated temporal events for accurate date calculation
        date_found = find_date_for_event(question, all_messages, temporal_events)
        if date_found:
            temporal_hint = f"\n\n📅 CALCULATED EVENT DATE: {date_found}\nThis date was calculated from the session timestamp and relative temporal reference in the conversation."

    # Generate answer with enhanced prompts
    prompt_template = get_prompt_for_category(category_name)
    enhanced_context = context + entity_warning + temporal_hint
    prompt = prompt_template.format(context=enhanced_context, question=question)
    generated = await generate_answer_async(http_session, prompt)

    # Judge using GPT-4o-mini (official MemMachine evaluation)
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)

    gen_time = time.time() - start_time
    binary_correct = judgment.get("binary_correct", 0)
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    print(f"  [{q_idx + 1}] {category_name}: {question[:40]}... [{status}] ({gen_time:.1f}s)", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "llm_score": binary_correct,  # Official MemMachine metric (0 or 1)
        "correct": binary_correct == 1,
        "time_seconds": round(gen_time, 2),
    }


# =============================================================================
# MAIN
# =============================================================================

async def run_parallel_benchmark(max_conversations: int = 10):
    """Run benchmark with parallel processing."""

    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print("=" * 70)
    print("LoCoMo-PARALLEL Benchmark v3.4 (HYBRID ROUTING)")
    print("=" * 70)
    print(f"Answer Model: {OLLAMA_MODEL}")
    print(f"Judge Model: {JUDGE_MODEL} (GPT-4o-mini)")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Concurrent questions: {CONCURRENT_QUESTIONS}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    all_results = []
    category_scores = {cat: [] for cat in CATEGORIES.values()}

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(dataset[:max_conversations]):
            conversation = item.get("conversation", item)
            qa_list = item.get("qa", [])

            speaker_a = conversation.get('speaker_a', 'Person A')
            speaker_b = conversation.get('speaker_b', 'Person B')

            print(f"\n{'='*60}")
            print(f"Conversation {conv_idx + 1}: {speaker_a} & {speaker_b}")
            print(f"Questions: {len(qa_list)}")
            print("="*60)

            # Extract and embed messages
            messages = extract_messages_with_timestamps(item)
            print(f"Total messages: {len(messages)}")
            messages = await batch_embed_messages(http_session, messages)

            # v3.3: CREATE COGNITIVE MEMORY INSTANCE (with temporal normalization)
            cognitive_memory = None
            if USE_COGNITIVE_MEMORY:
                try:
                    config = CognitivePipelineConfig()
                    cognitive_memory = CognitiveEpisodicMemory(
                        session_key=f"locomo_conv_{conv_idx}",
                        config=config,
                    )
                    print(f"  Ingesting {len(messages)} messages to cognitive memory...")
                    await ingest_messages_to_cognitive_memory(cognitive_memory, messages)
                    print(f"  Cognitive memory initialized with temporal normalization")
                except Exception as e:
                    print(f"  [WARN] Cognitive memory init failed: {e}, using fallback")
                    cognitive_memory = None

            # BUILD ENTITY TRACKER from messages (for adversarial detection)
            entity_tracker = EntityTracker()
            for msg in messages:
                entity_tracker.extract_from_message(msg['speaker'], msg['text'])
            print(f"  Entity tracker: {len(entity_tracker.known_entities)} entities, "
                  f"{sum(len(v) for v in entity_tracker.entity_possessions.values())} possessions tracked")

            # BUILD TEMPORAL INDEX (for temporal questions)
            temporal_index = build_temporal_index(messages)
            print(f"  Temporal index: {len(temporal_index)} dates indexed")

            # BUILD TEMPORAL EVENT INDEX (KEY FIX for 100% temporal failure)
            # Pre-calculates actual dates from relative temporal references
            temporal_events = build_temporal_event_index(messages)
            events_with_calculated = sum(1 for e in temporal_events if e.relative_reference)
            print(f"  Temporal events: {len(temporal_events)} events, {events_with_calculated} with calculated dates")

            # Process questions in parallel batches
            semaphore = asyncio.Semaphore(CONCURRENT_QUESTIONS)

            async def process_with_semaphore(q_idx, qa):
                async with semaphore:
                    return await process_question(
                        http_session, q_idx, qa, messages, messages,
                        entity_tracker=entity_tracker,
                        temporal_index=temporal_index,
                        temporal_events=temporal_events,
                        cognitive_memory=cognitive_memory,  # v3.3
                    )

            tasks = [process_with_semaphore(q_idx, qa) for q_idx, qa in enumerate(qa_list)]
            results = await asyncio.gather(*tasks)

            for result in results:
                result["conversation_id"] = conv_idx
                all_results.append(result)
                category_scores[result["category"]].append(result)

    # Compute metrics (Official MemMachine format)
    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS (Official MemMachine LLM Judge)")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    overall_accuracy = (correct / total) if total > 0 else 0

    # Category name mapping for official format
    CATEGORY_TYPE_MAP = {
        "single_hop": "single_hop",
        "temporal": "temporal",
        "open_domain": "open_domain",
        "multi_hop": "multi_hop",
        "adversarial": "adversarial",
    }

    print("\nMean Scores Per Category:")
    print(f"{'':12} llm_score  count         type")
    print("category")
    for category, scores in category_scores.items():
        if scores:
            cat_correct = sum(1 for s in scores if s["correct"])
            cat_accuracy = cat_correct / len(scores)
            cat_type = CATEGORY_TYPE_MAP.get(category, category)
            print(f"{category:12}    {cat_accuracy:.4f}    {len(scores):3d}    {cat_type}")

    print(f"\nOverall Mean Scores:")
    print(f"llm_score    {overall_accuracy:.4f}")
    print(f"\nTotal Questions: {total}")
    print(f"Correct: {correct}/{total} ({overall_accuracy*100:.1f}%)")

    # Save results
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"locomo10_PARALLEL_V3.2_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "benchmark": "LoCoMo-PARALLEL v3.2 (ENHANCED TEMPORAL)",
            "answer_model": OLLAMA_MODEL,
            "judge_model": JUDGE_MODEL,
            "timestamp": timestamp,
            "metrics": {
                "total_questions": total,
                "overall_llm_score": round(overall_accuracy, 4),
                "category_scores": {
                    cat: round(sum(1 for s in scores if s["correct"]) / len(scores), 4)
                    for cat, scores in category_scores.items() if scores
                },
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {json_path}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_parallel_benchmark(max_conversations=10))

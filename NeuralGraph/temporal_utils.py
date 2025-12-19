"""Temporal Utilities - Shared date resolution and temporal indexing.

This module provides core temporal processing that should be applied during:
1. Message ingestion (resolve_relative_dates, generate_temporal_tokens)
2. Query processing (expand_temporal_query)

These utilities ensure consistent temporal handling across the entire system,
making temporal queries work correctly in production AND benchmarks.

THE KEY INSIGHT:
================
When someone says "yesterday" on May 8th, 2023, that means May 7th, 2023.
This resolution must happen ONCE at ingestion time with the correct message
timestamp, not at query time when we've lost the original context.

Usage:
    from memmachine.neural_graph.temporal_utils import (
        resolve_relative_dates,
        generate_temporal_tokens,
        expand_temporal_query,
        parse_datetime_flexible,
        MONTH_NAMES,
        MONTH_NAMES_REV,
    )

    # During ingestion:
    message_date = parse_datetime_flexible("1:56 pm on 8 May, 2023")
    resolved_content, dates_meta = resolve_relative_dates(text, message_date)
    temporal_data = generate_temporal_tokens(message_date, text)

    # During query:
    expanded_query = expand_temporal_query("When did X happen in May 2023?")
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


# =============================================================================
# SHARED CONSTANTS
# =============================================================================

MONTH_NAMES: dict[str, int] = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12
}

MONTH_NAMES_REV: dict[int, str] = {v: k for k, v in MONTH_NAMES.items()}

DAY_NAMES: list[str] = [
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'
]

DAY_NAMES_REV: list[str] = [d.upper() for d in DAY_NAMES]

# Number words for parsing
NUMBER_WORDS: dict[str, int] = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12
}


# =============================================================================
# DATE PARSING
# =============================================================================

def parse_datetime_flexible(dt_str: str | None) -> datetime | None:
    """Parse datetime from various formats.

    Supports:
    - LoCoMo format: "1:56 pm on 8 May, 2023"
    - ISO format: "2023-05-08T13:56:00"
    - Simple date: "8 May 2023", "May 8, 2023"

    Args:
        dt_str: Datetime string to parse

    Returns:
        Parsed datetime or None if parsing fails
    """
    if not dt_str:
        return None

    dt_str = dt_str.strip()

    # LoCoMo format: "1:56 pm on 8 May, 2023"
    pattern = r'(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(\w+),?\s*(\d{4})'
    match = re.match(pattern, dt_str, re.IGNORECASE)
    if match:
        hour, minute, ampm, day, month_name, year = match.groups()
        month = MONTH_NAMES.get(month_name.lower())
        if month:
            hour_int = int(hour)
            if ampm.lower() == 'pm' and hour_int != 12:
                hour_int += 12
            elif ampm.lower() == 'am' and hour_int == 12:
                hour_int = 0
            return datetime(int(year), month, int(day), hour_int, int(minute))

    # ISO format: "2023-05-08T13:56:00" or "2023-05-08"
    try:
        if 'T' in dt_str:
            return datetime.fromisoformat(dt_str)
        elif re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
            return datetime.fromisoformat(dt_str)
    except ValueError:
        pass

    # Simple date: "8 May 2023"
    simple_pattern = r'(\d{1,2})\s+(\w+)\s+(\d{4})'
    match = re.match(simple_pattern, dt_str, re.IGNORECASE)
    if match:
        day, month_name, year = match.groups()
        month = MONTH_NAMES.get(month_name.lower())
        if month:
            return datetime(int(year), month, int(day))

    # US format: "May 8, 2023"
    us_pattern = r'(\w+)\s+(\d{1,2}),?\s*(\d{4})'
    match = re.match(us_pattern, dt_str, re.IGNORECASE)
    if match:
        month_name, day, year = match.groups()
        month = MONTH_NAMES.get(month_name.lower())
        if month:
            return datetime(int(year), month, int(day))

    return None


# =============================================================================
# RELATIVE DATE RESOLUTION (INGESTION TIME)
# =============================================================================

def resolve_relative_dates(content: str, message_date: datetime | None) -> tuple[str, dict[str, Any]]:
    """Resolve ALL relative date references in content based on message timestamp.

    This function annotates content with resolved dates in [= DATE] format,
    making it easy for retrieval and LLM prompts to use the correct dates.

    CRITICAL: This must be called at INGESTION time with the original message
    timestamp, not at query time.

    Args:
        content: The message content to process
        message_date: The datetime when the message was sent

    Returns:
        tuple: (resolved_content, resolved_dates_metadata)
        - resolved_content: Content with [= DATE] annotations
        - resolved_dates_metadata: Dict of detected patterns and their resolved dates

    Example:
        >>> resolve_relative_dates("I went there yesterday", datetime(2023, 5, 8))
        ("I went there yesterday [= 7 May 2023]", {"yesterday": "7 May 2023", ...})
    """
    if not message_date or not content:
        return content, {}

    result = content
    resolved_dates: dict[str, Any] = {}
    content_lower = content.lower()

    # 1. "yesterday" / "last night" -> specific date
    if 'yesterday' in content_lower or 'last night' in content_lower:
        yesterday = message_date - timedelta(days=1)
        date_str = f"{yesterday.day} {MONTH_NAMES_REV[yesterday.month].title()} {yesterday.year}"
        resolved_dates['yesterday'] = date_str
        result = re.sub(r'\byesterday\b', f'yesterday [= {date_str}]', result, flags=re.IGNORECASE)
        result = re.sub(r'\blast night\b', f'last night [= {date_str}]', result, flags=re.IGNORECASE)

    # 2. "last weekend" -> specific weekend dates
    if 'last weekend' in content_lower:
        days_since_saturday = (message_date.weekday() + 2) % 7
        if days_since_saturday == 0:
            days_since_saturday = 7
        last_saturday = message_date - timedelta(days=days_since_saturday)
        last_sunday = last_saturday + timedelta(days=1)
        date_str = f"{last_saturday.day}-{last_sunday.day} {MONTH_NAMES_REV[last_saturday.month].title()} {last_saturday.year}"
        resolved_dates['last_weekend'] = date_str
        result = re.sub(r'\blast weekend\b', f'last weekend [= {date_str}]', result, flags=re.IGNORECASE)

    # 3. "last week" -> week start date (but NOT if followed by "end")
    if re.search(r'\blast week\b(?!end)', content_lower):
        last_week = message_date - timedelta(days=7)
        week_start = last_week - timedelta(days=last_week.weekday())
        date_str = f"week of {week_start.day} {MONTH_NAMES_REV[week_start.month].title()} {week_start.year}"
        resolved_dates['last_week'] = date_str
        result = re.sub(r'\blast week\b(?!end)', f'last week [= {date_str}]', result, flags=re.IGNORECASE)

    # 4. "this week" -> current week
    if 'this week' in content_lower:
        week_start = message_date - timedelta(days=message_date.weekday())
        date_str = f"week of {week_start.day} {MONTH_NAMES_REV[week_start.month].title()} {week_start.year}"
        resolved_dates['this_week'] = date_str
        result = re.sub(r'\bthis week\b', f'this week [= {date_str}]', result, flags=re.IGNORECASE)

    # 5. "last year" -> specific year
    if 'last year' in content_lower:
        last_year = message_date.year - 1
        resolved_dates['last_year'] = str(last_year)
        result = re.sub(r'\blast year\b', f'last year [= {last_year}]', result, flags=re.IGNORECASE)

    # 6. "last month" -> specific month
    if 'last month' in content_lower:
        if message_date.month == 1:
            last_month = 12
            last_month_year = message_date.year - 1
        else:
            last_month = message_date.month - 1
            last_month_year = message_date.year
        date_str = f"{MONTH_NAMES_REV[last_month].title()} {last_month_year}"
        resolved_dates['last_month'] = date_str
        result = re.sub(r'\blast month\b', f'last month [= {date_str}]', result, flags=re.IGNORECASE)

    # 7. "next month" -> specific month
    if 'next month' in content_lower:
        if message_date.month == 12:
            next_month = 1
            next_month_year = message_date.year + 1
        else:
            next_month = message_date.month + 1
            next_month_year = message_date.year
        date_str = f"{MONTH_NAMES_REV[next_month].title()} {next_month_year}"
        resolved_dates['next_month'] = date_str
        result = re.sub(r'\bnext month\b', f'next month [= {date_str}]', result, flags=re.IGNORECASE)

    # 8. "a few weeks ago" / "few weeks ago" -> ~3 weeks back
    if re.search(r'\b(a )?few weeks? ago\b', content_lower):
        few_weeks = message_date - timedelta(weeks=3)
        date_str = f"around {few_weeks.day} {MONTH_NAMES_REV[few_weeks.month].title()} {few_weeks.year}"
        resolved_dates['few_weeks_ago'] = date_str
        result = re.sub(r'\b(a )?few weeks? ago\b', f'a few weeks ago [= {date_str}]', result, flags=re.IGNORECASE)

    # 9. "two weeks ago" / "2 weeks ago"
    two_weeks_match = re.search(r'\b(two|2) weeks? ago\b', content_lower)
    if two_weeks_match:
        two_weeks = message_date - timedelta(weeks=2)
        date_str = f"around {two_weeks.day} {MONTH_NAMES_REV[two_weeks.month].title()} {two_weeks.year}"
        resolved_dates['two_weeks_ago'] = date_str
        result = re.sub(r'\b(two|2) weeks? ago\b', f'two weeks ago [= {date_str}]', result, flags=re.IGNORECASE)

    # 10. "X days ago" (numeric)
    days_ago_match = re.search(r'\b(\d+) days? ago\b', content_lower)
    if days_ago_match:
        num_days = int(days_ago_match.group(1))
        target_date = message_date - timedelta(days=num_days)
        date_str = f"{target_date.day} {MONTH_NAMES_REV[target_date.month].title()} {target_date.year}"
        resolved_dates[f'{num_days}_days_ago'] = date_str
        result = re.sub(
            r'\b(\d+) days? ago\b',
            lambda m: f'{m.group(1)} days ago [= {date_str}]',
            result, flags=re.IGNORECASE
        )

    # 11. "two days ago" (word)
    if 'two days ago' in content_lower:
        two_days = message_date - timedelta(days=2)
        date_str = f"{two_days.day} {MONTH_NAMES_REV[two_days.month].title()} {two_days.year}"
        resolved_dates['two_days_ago'] = date_str
        result = re.sub(r'\btwo days? ago\b', f'two days ago [= {date_str}]', result, flags=re.IGNORECASE)

    # 12. Day names: "last Monday", "last Friday", etc.
    for i, day in enumerate(DAY_NAMES):
        pattern = rf'\blast {day}\b'
        if re.search(pattern, content_lower):
            days_back = (message_date.weekday() - i) % 7
            if days_back == 0:
                days_back = 7  # Go back a full week if same day
            target_date = message_date - timedelta(days=days_back)
            date_str = f"{target_date.day} {MONTH_NAMES_REV[target_date.month].title()} {target_date.year}"
            resolved_dates[f'last_{day}'] = date_str
            result = re.sub(
                pattern,
                f'last {day} [= {date_str}]',
                result, flags=re.IGNORECASE
            )

    # 13. "ten years ago", "X years ago"
    years_ago_match = re.search(r'\b(ten|\d+) years? ago\b', content_lower)
    if years_ago_match:
        num_str = years_ago_match.group(1)
        num_years = NUMBER_WORDS.get(num_str, int(num_str) if num_str.isdigit() else 0)
        if num_years:
            target_year = message_date.year - num_years
            resolved_dates[f'{num_years}_years_ago'] = str(target_year)
            result = re.sub(
                r'\b(ten|\d+) years? ago\b',
                lambda m: f'{m.group(1)} years ago [= {target_year}]',
                result, flags=re.IGNORECASE
            )

    # Store the message datetime for reference
    resolved_dates['message_datetime'] = message_date.strftime("%d %B %Y")

    return result, resolved_dates


# =============================================================================
# TEMPORAL TOKEN GENERATION (INGESTION TIME)
# =============================================================================

def generate_temporal_tokens(message_date: datetime | None, content: str) -> dict[str, Any]:
    """Generate explicit temporal tokens for BM25 indexing.

    These tokens are appended to content so BM25 can match:
    "March 2023" in query -> "MONTH_MARCH YEAR_2023" in indexed content

    Args:
        message_date: When the message was sent
        content: The message content (used to detect relative dates)

    Returns:
        dict with:
        - date_tokens: list of searchable date strings
        - temporal_metadata: structured fields for filtering

    Example:
        >>> data = generate_temporal_tokens(datetime(2023, 5, 8), "went camping")
        >>> data["date_tokens"]
        ['DATE_2023-05-08', 'YEAR_2023', 'MONTH_05', 'MONTH_MAY', ...]
    """
    if not message_date:
        return {"date_tokens": [], "temporal_metadata": {}}

    tokens: list[str] = []
    metadata: dict[str, Any] = {}

    # DATE_YYYY_MM_DD format
    date_iso = message_date.strftime("%Y-%m-%d")
    tokens.append(f"DATE_{date_iso}")
    metadata["date_iso"] = date_iso

    # YEAR token
    year = message_date.year
    tokens.append(f"YEAR_{year}")
    metadata["year"] = year

    # MONTH token (both number and name)
    month = message_date.month
    month_name = MONTH_NAMES_REV.get(month, "").upper()
    tokens.append(f"MONTH_{month:02d}")
    tokens.append(f"MONTH_{month_name}")
    metadata["month"] = month
    metadata["month_name"] = month_name

    # DAY token
    day = message_date.day
    tokens.append(f"DAY_{day:02d}")
    metadata["day"] = day

    # DAY OF WEEK token
    dow = message_date.weekday()
    dow_name = DAY_NAMES_REV[dow]
    tokens.append(f"DOW_{dow_name}")
    metadata["day_of_week"] = dow_name

    # WEEK number
    week_num = message_date.isocalendar()[1]
    tokens.append(f"WEEK_{week_num:02d}")
    metadata["week_number"] = week_num

    # Quarter
    quarter = (month - 1) // 3 + 1
    tokens.append(f"Q{quarter}_{year}")
    metadata["quarter"] = f"Q{quarter}"

    # Season (Northern hemisphere)
    if month in [12, 1, 2]:
        season = "WINTER"
    elif month in [3, 4, 5]:
        season = "SPRING"
    elif month in [6, 7, 8]:
        season = "SUMMER"
    else:
        season = "FALL"
    tokens.append(f"SEASON_{season}")
    metadata["season"] = season

    # Detect relative date phrases in content and add resolved tokens
    relative_resolutions = _extract_relative_dates_for_tokens(content, message_date)
    for rel_type, resolved_date in relative_resolutions:
        tokens.append(f"REL_{rel_type}_RESOLVED_{resolved_date}")

    return {"date_tokens": tokens, "temporal_metadata": metadata}


def _extract_relative_dates_for_tokens(content: str, message_date: datetime) -> list[tuple[str, str]]:
    """Extract relative date phrases and resolve them for token generation."""
    results: list[tuple[str, str]] = []
    content_lower = content.lower()

    # Yesterday
    if "yesterday" in content_lower:
        resolved = (message_date - timedelta(days=1)).strftime("%Y-%m-%d")
        results.append(("YESTERDAY", resolved))

    # Last week
    if "last week" in content_lower:
        resolved = (message_date - timedelta(days=7)).strftime("%Y-%m-%d")
        results.append(("LAST_WEEK", resolved))

    # Last month
    if "last month" in content_lower:
        if message_date.month == 1:
            resolved_month = 12
            resolved_year = message_date.year - 1
        else:
            resolved_month = message_date.month - 1
            resolved_year = message_date.year
        results.append(("LAST_MONTH", f"{resolved_year}-{resolved_month:02d}"))

    # This weekend / last weekend
    if "weekend" in content_lower:
        days_since_saturday = (message_date.weekday() + 2) % 7
        saturday = message_date - timedelta(days=days_since_saturday)
        results.append(("WEEKEND", saturday.strftime("%Y-%m-%d")))

    # X days ago
    days_ago_match = re.search(r'(\d+|two|three|four|five|six|seven)\s+days?\s+ago', content_lower)
    if days_ago_match:
        num_str = days_ago_match.group(1)
        num = NUMBER_WORDS.get(num_str, int(num_str) if num_str.isdigit() else 0)
        if num > 0:
            resolved = (message_date - timedelta(days=num)).strftime("%Y-%m-%d")
            results.append((f"{num}_DAYS_AGO", resolved))

    return results


# =============================================================================
# QUERY EXPANSION (QUERY TIME)
# =============================================================================

def expand_temporal_query(query: str) -> str:
    """Expand a temporal query with date tokens for BM25 matching.

    This ensures queries like "May 2023" match content indexed with
    "MONTH_MAY YEAR_2023" tokens.

    Args:
        query: The original query text

    Returns:
        Query with appended temporal tokens

    Example:
        >>> expand_temporal_query("When did X happen in May 2023?")
        "When did X happen in May 2023? MONTH_MAY YEAR_2023"
    """
    query_lower = query.lower()
    tokens: list[str] = []

    # Extract year references
    year_matches = re.findall(r'\b(20\d{2})\b', query)
    for year in year_matches:
        tokens.append(f"YEAR_{year}")

    # Extract month references
    for month_name, month_num in MONTH_NAMES.items():
        if month_name in query_lower:
            tokens.append(f"MONTH_{month_name.upper()}")

    # Extract day of week references
    for dow in DAY_NAMES:
        if dow in query_lower:
            tokens.append(f"DOW_{dow.upper()}")

    # Season references
    season_map = {'spring': 'SPRING', 'summer': 'SUMMER', 'fall': 'FALL',
                  'autumn': 'FALL', 'winter': 'WINTER'}
    for season_name, season_token in season_map.items():
        if season_name in query_lower:
            tokens.append(f"SEASON_{season_token}")

    # Relative time references (help match resolved tokens)
    if 'yesterday' in query_lower:
        tokens.append("REL_YESTERDAY")
    if 'last week' in query_lower:
        tokens.append("REL_LAST_WEEK")
    if 'last month' in query_lower:
        tokens.append("REL_LAST_MONTH")

    if tokens:
        return query + " " + " ".join(tokens)
    return query


# =============================================================================
# QUERY MODE INFERENCE
# =============================================================================

def infer_query_mode(question: str) -> str:
    """Infer query mode from question text for prompt routing.

    Returns: "TEMPORAL", "INFERENTIAL", "AGGREGATION", "ADVERSARIAL", or "STRICT"

    This determines which prompt template and retrieval strategy to use.

    These patterns are UNIVERSAL and work across any domain - not tied to any
    specific benchmark or dataset.

    Args:
        question: The query text

    Returns:
        One of the mode strings
    """
    q = question.lower().strip()

    # === TEMPORAL patterns (highest priority) ===
    # Comprehensive patterns for time-based questions
    temporal_starters = [
        # Basic when questions
        "when ", "when's ", "when did ", "when does ", "when was ", "when were ",
        "when will ", "when is ", "when are ", "when has ", "when have ",
        # Date/time specific questions
        "what date", "what day", "what year", "what month", "what week",
        "what time", "what hour", "what season", "what period",
        # Specific time frame questions
        "on what day", "in what year", "in what month", "during what",
        "at what time", "on which day", "in which year", "in which month",
        # Duration starters
        "how long ", "for how long", "how much time",
        # Sequence starters
        "after what", "before what", "since when", "until when",
        "how soon", "how recently", "how early", "how late",
    ]
    temporal_contains = [
        # Duration patterns
        "how long ago", "how long has", "how long have", "how long did",
        "how long was", "how long were", "how long will", "how long does",
        # Time unit questions
        "how many days", "how many weeks", "how many months", "how many years",
        "how many hours", "how many minutes", "how many seconds",
        # Elapsed time patterns
        "since when", "until when", "how much time", "amount of time",
        " passed between", "weeks passed", "days passed", "months passed",
        "years passed", "time elapsed", "time since", "time until",
        # Relative time patterns
        "ago did", "ago was", "ago were", "ago has", "ago have",
        "years ago", "months ago", "weeks ago", "days ago", "hours ago",
        # Sequence patterns
        "happened before", "happened after", "came before", "came after",
        "prior to", "subsequent to", "following the", "preceding the",
        "in the wake of", "leading up to", "in advance of",
        # Specific time references
        "last week", "this week", "next week", "last month", "this month",
        "next month", "last year", "this year", "next year",
        "yesterday", "today", "tomorrow", "last night", "tonight",
        "this morning", "this afternoon", "this evening",
        # Date patterns
        "what date did", "on what date", "which date", "which day",
        # Frequency in time
        "how often", "how frequently", "how regularly",
        # Deadline/schedule
        "by when", "due when", "deadline", "scheduled for",
        # Age/duration
        "how old", "for how many", "lasted how long",
    ]
    for starter in temporal_starters:
        if q.startswith(starter):
            return "TEMPORAL"
    for pattern in temporal_contains:
        if pattern in q:
            return "TEMPORAL"

    # === AGGREGATION patterns: questions expecting multiple answers ===
    # Universal patterns for list/collection questions
    aggregation_patterns = [
        # Activity/hobby questions
        "what activities", "what hobbies", "what things", "what items",
        "what tasks", "what chores", "what duties", "what responsibilities",
        # Experience questions
        "where has", "where have", "where did", "where does",
        "what places", "which places", "what locations", "what destinations",
        # Media/content questions
        "what books", "what movies", "what shows", "what films",
        "what songs", "what music", "what albums", "what podcasts",
        "what games", "what apps", "what programs", "what software",
        # Event questions
        "what events", "what occasions", "what ceremonies", "what celebrations",
        "what meetings", "what appointments", "what gatherings",
        # Type/category questions
        "what types", "what kinds", "what sorts", "what categories",
        "what varieties", "what forms", "what styles",
        # Collection questions
        "how many times", "how many different", "how many various",
        "list all", "list the", "name all", "name the",
        "what are all", "what were all", "who are all", "who were all",
        # Multi-item questions
        "what are the", "what were the", "who are the", "who were the",
        "all of the", "each of the", "every one of",
        # Enumeration patterns
        "what things does", "what things did", "everything that",
        "all that", "everyone who", "everybody who",
        # Favorite collections
        "favorite things", "favorite activities", "favorite places",
        "preferred", "top choices", "main interests",
    ]
    for pattern in aggregation_patterns:
        if pattern in q:
            return "AGGREGATION"
    # "how many" without time units = aggregation
    if "how many" in q and not any(t in q for t in ["days", "weeks", "months", "years", "hours", "minutes", "seconds"]):
        return "AGGREGATION"

    # === INFERENTIAL patterns: inference-required questions ===
    # Universal patterns requiring reasoning/inference
    inferential_starters = [
        # Modal verbs indicating inference
        "would ", "could ", "might ", "should ", "may ",
        # Causation questions
        "why ", "why's ", "why did ", "why does ", "why is ", "why was ",
        "how come ", "what caused ", "what made ", "what led to ",
        # Hypothetical questions
        "is it likely", "is it possible", "is it probable",
        "would it be", "could it be", "might it be",
        # Opinion/judgment questions
        "do you think", "would you say", "would you agree",
    ]
    inferential_contains = [
        # Probability/likelihood patterns
        " likely ", " likely?", " probably ", " probably?",
        " possibly ", " possibly?", " perhaps ", " maybe ",
        # Inference indicators
        " suggest ", " imply ", " indicate ", " predict ",
        " infer ", " deduce ", " conclude ", " assume ",
        # Personality/character questions
        "what kind of person", "what type of person", "what sort of person",
        "what personality", "what traits", "what characteristics",
        "what qualities", "what attributes", "what tendencies",
        # Career/interest inference
        "what fields", "what career", "what job", "what profession",
        "what industry", "what sector", "what domain",
        # Preference inference
        "be considered ", "be open to ", "be interested in ",
        "be willing to", "be able to", "be inclined to",
        # Description/characterization
        "would you describe", "how would you describe", "how would you characterize",
        "best describes", "accurately describes",
        # Future actions/goals
        " pursue ", " pursuing ", " aspire ", " aspiring ",
        " strive ", " striving ", " aim ", " aiming ",
        # Speculation patterns
        "what might ", " might be", "what underlying", "what hidden",
        "what unspoken", "what implicit",
        # Device/platform inference
        "what console", "what device", "what platform", "what system",
        "what equipment", "what tool", "what technology",
        # Relationship/social inference
        "what nickname", "what pet", "what pets", "how close",
        "how well do", "relationship with",
        # Negated possibilities
        " wouldn't ", " couldn't ", " shouldn't ", " mightn't ",
        # Reason/motivation inference
        "motivation for", "reason for", "purpose of", "goal of",
        "intent behind", "meaning of",
        # Emotional/mental state inference
        "how does .* feel", "what does .* think", "what does .* believe",
        "attitude toward", "opinion on", "view of",
    ]
    for starter in inferential_starters:
        if q.startswith(starter):
            return "INFERENTIAL"
    for pattern in inferential_contains:
        if pattern in q:
            return "INFERENTIAL"

    # === ADVERSARIAL patterns: negation and unanswerable detection ===
    # Universal patterns for negation-based or tricky questions
    adversarial_patterns = [
        # Direct negations
        " not ", "n't ", " never ", " no ", " none ", " nothing ",
        " nobody ", " nowhere ", " neither ", " nor ",
        # Exception patterns
        " except ", " other than ", " apart from ", " besides ",
        " excluding ", " but not ", " save for ",
        # Absence patterns
        " without ", " lacking ", " missing ", " absent ",
        # Failure patterns
        " fail", " failed ", " failing ", "didn't ", "doesn't ",
        "wasn't", "weren't", "hasn't", "haven't", "hadn't",
        "won't", "wouldn't", "can't", "couldn't", "shouldn't",
        # Contradiction patterns
        " instead of ", " rather than ", " as opposed to ",
        # Denial patterns
        " deny ", " denied ", " refuse ", " refused ",
        " reject ", " rejected ",
        # Non-existence patterns
        " isn't ", " aren't ", " ain't ",
        # Counter-factual patterns
        " if not ", " unless ", " otherwise ",
        # Skeptical patterns
        " really ", " actually ", " truly ", " genuinely ",
    ]
    for pattern in adversarial_patterns:
        if pattern in q:
            return "ADVERSARIAL"

    # === Default to STRICT (explicit fact queries) ===
    return "STRICT"


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def preprocess_message_for_indexing(
    content: str,
    datetime_str: str | None = None,
    message_date: datetime | None = None,
) -> dict[str, Any]:
    """Preprocess a message for indexing with full temporal enhancement.

    This is the main entry point for ingestion pipelines.

    Args:
        content: The message text
        datetime_str: String representation of datetime (will be parsed)
        message_date: Already-parsed datetime (takes precedence over datetime_str)

    Returns:
        dict with:
        - content_with_tokens: Enhanced content with temporal tokens
        - resolved_content: Content with [= DATE] annotations
        - original_content: The original unmodified content
        - date_tokens: List of temporal tokens
        - temporal_metadata: Structured temporal info
        - resolved_dates: Dict of resolved relative dates
    """
    # Parse datetime if needed
    if message_date is None and datetime_str:
        message_date = parse_datetime_flexible(datetime_str)

    # Resolve relative dates
    resolved_content, resolved_dates = resolve_relative_dates(content, message_date)

    # Generate temporal tokens
    temporal_data = generate_temporal_tokens(message_date, content)
    date_tokens = temporal_data["date_tokens"]
    temporal_metadata = temporal_data["temporal_metadata"]

    # Build enhanced content
    content_with_tokens = resolved_content
    if date_tokens:
        content_with_tokens += " " + " ".join(date_tokens)

    return {
        "content_with_tokens": content_with_tokens,
        "resolved_content": resolved_content,
        "original_content": content,
        "date_tokens": date_tokens,
        "temporal_metadata": temporal_metadata,
        "resolved_dates": resolved_dates,
    }

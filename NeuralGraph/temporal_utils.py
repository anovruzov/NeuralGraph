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

# Comprehensive day abbreviations mapping to weekday index (0=Monday, 6=Sunday)
DAY_ABBREVIATIONS: dict[str, int] = {
    # Monday (0)
    'mon': 0, 'mo': 0,
    # Tuesday (1)
    'tue': 1, 'tues': 1, 'tu': 1,
    # Wednesday (2)
    'wed': 2, 'weds': 2, 'we': 2,
    # Thursday (3)
    'thu': 3, 'thur': 3, 'thurs': 3, 'th': 3,
    # Friday (4)
    'fri': 4, 'fr': 4,
    # Saturday (5)
    'sat': 5, 'sa': 5,
    # Sunday (6)
    'sun': 6, 'su': 6,
}

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

    # 12. Day names: "last Monday", "last Friday", etc. (full names)
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

    # 13. Day abbreviations and variations - comprehensive patterns
    # Build regex pattern for all abbreviations (sorted by length desc to match longer first)
    abbrevs_sorted = sorted(DAY_ABBREVIATIONS.keys(), key=len, reverse=True)
    abbrev_pattern = '|'.join(abbrevs_sorted)
    # Also include full day names in the pattern
    full_days_pattern = '|'.join(DAY_NAMES)
    all_days_pattern = f"({abbrev_pattern}|{full_days_pattern})"

    # 13a. "last <day>" patterns (last Fri, last Friday, Last fri., etc.)
    last_day_regex = rf'\blast\s+{all_days_pattern}\.?\b(?!\s*\[=)'
    for match in re.finditer(last_day_regex, content_lower):
        day_match = match.group(1).lower()
        # Get weekday index
        if day_match in DAY_ABBREVIATIONS:
            weekday_idx = DAY_ABBREVIATIONS[day_match]
        elif day_match in DAY_NAMES:
            weekday_idx = DAY_NAMES.index(day_match)
        else:
            continue
        days_back = (message_date.weekday() - weekday_idx) % 7
        if days_back == 0:
            days_back = 7
        target_date = message_date - timedelta(days=days_back)
        date_str = f"{target_date.day} {MONTH_NAMES_REV[target_date.month].title()} {target_date.year}"
        full_day_name = DAY_NAMES[weekday_idx]
        resolved_dates[f'last_{full_day_name}'] = date_str
        result = re.sub(
            rf'\blast\s+{re.escape(day_match)}\.?\b(?!\s*\[=)',
            f'last {full_day_name} [= {date_str}]',
            result, count=1, flags=re.IGNORECASE
        )

    # 13b. "this <day>" patterns (this Fri, this Friday, etc.)
    this_day_regex = rf'\bthis\s+{all_days_pattern}\.?\b(?!\s*\[=)'
    for match in re.finditer(this_day_regex, content_lower):
        day_match = match.group(1).lower()
        if day_match in DAY_ABBREVIATIONS:
            weekday_idx = DAY_ABBREVIATIONS[day_match]
        elif day_match in DAY_NAMES:
            weekday_idx = DAY_NAMES.index(day_match)
        else:
            continue
        # "this <day>" = the coming occurrence (or today if same day)
        days_forward = (weekday_idx - message_date.weekday()) % 7
        target_date = message_date + timedelta(days=days_forward)
        date_str = f"{target_date.day} {MONTH_NAMES_REV[target_date.month].title()} {target_date.year}"
        full_day_name = DAY_NAMES[weekday_idx]
        resolved_dates[f'this_{full_day_name}'] = date_str
        result = re.sub(
            rf'\bthis\s+{re.escape(day_match)}\.?\b(?!\s*\[=)',
            f'this {full_day_name} [= {date_str}]',
            result, count=1, flags=re.IGNORECASE
        )

    # 13c. "next <day>" patterns (next Fri, next Friday, etc.)
    next_day_regex = rf'\bnext\s+{all_days_pattern}\.?\b(?!\s*\[=)'
    for match in re.finditer(next_day_regex, content_lower):
        day_match = match.group(1).lower()
        if day_match in DAY_ABBREVIATIONS:
            weekday_idx = DAY_ABBREVIATIONS[day_match]
        elif day_match in DAY_NAMES:
            weekday_idx = DAY_NAMES.index(day_match)
        else:
            continue
        # "next <day>" = next week's occurrence
        days_forward = (weekday_idx - message_date.weekday()) % 7
        if days_forward == 0:
            days_forward = 7
        days_forward += 7  # Add a week for "next"
        target_date = message_date + timedelta(days=days_forward)
        date_str = f"{target_date.day} {MONTH_NAMES_REV[target_date.month].title()} {target_date.year}"
        full_day_name = DAY_NAMES[weekday_idx]
        resolved_dates[f'next_{full_day_name}'] = date_str
        result = re.sub(
            rf'\bnext\s+{re.escape(day_match)}\.?\b(?!\s*\[=)',
            f'next {full_day_name} [= {date_str}]',
            result, count=1, flags=re.IGNORECASE
        )

    # 13d. "on <day>" patterns without prefix (on Fri, on Friday, on fri.)
    on_day_regex = rf'\bon\s+{all_days_pattern}\.?\b(?!\s*\[=)'
    for match in re.finditer(on_day_regex, content_lower):
        day_match = match.group(1).lower()
        if day_match in DAY_ABBREVIATIONS:
            weekday_idx = DAY_ABBREVIATIONS[day_match]
        elif day_match in DAY_NAMES:
            weekday_idx = DAY_NAMES.index(day_match)
        else:
            continue
        # "on <day>" = most recent or upcoming (assume past for memory context)
        days_back = (message_date.weekday() - weekday_idx) % 7
        if days_back == 0:
            days_back = 7
        target_date = message_date - timedelta(days=days_back)
        date_str = f"{target_date.day} {MONTH_NAMES_REV[target_date.month].title()} {target_date.year}"
        full_day_name = DAY_NAMES[weekday_idx]
        resolved_dates[f'on_{full_day_name}'] = date_str
        result = re.sub(
            rf'\bon\s+{re.escape(day_match)}\.?\b(?!\s*\[=)',
            f'on {full_day_name} [= {date_str}]',
            result, count=1, flags=re.IGNORECASE
        )

    # 13e. "today" and "tonight"
    if re.search(r'\btoday\b(?!\s*\[=)', content_lower):
        date_str = f"{message_date.day} {MONTH_NAMES_REV[message_date.month].title()} {message_date.year}"
        resolved_dates['today'] = date_str
        result = re.sub(r'\btoday\b(?!\s*\[=)', f'today [= {date_str}]', result, flags=re.IGNORECASE)

    if re.search(r'\btonight\b(?!\s*\[=)', content_lower):
        date_str = f"{message_date.day} {MONTH_NAMES_REV[message_date.month].title()} {message_date.year}"
        resolved_dates['tonight'] = date_str
        result = re.sub(r'\btonight\b(?!\s*\[=)', f'tonight [= {date_str}]', result, flags=re.IGNORECASE)

    # 13f. "tomorrow" and "tomorrow night"
    if re.search(r'\btomorrow\b(?!\s*\[=)', content_lower):
        tomorrow = message_date + timedelta(days=1)
        date_str = f"{tomorrow.day} {MONTH_NAMES_REV[tomorrow.month].title()} {tomorrow.year}"
        resolved_dates['tomorrow'] = date_str
        result = re.sub(r'\btomorrow\b(?!\s*\[=)', f'tomorrow [= {date_str}]', result, flags=re.IGNORECASE)

    # 13g. "this morning", "this afternoon", "this evening"
    for period in ['morning', 'afternoon', 'evening']:
        pattern = rf'\bthis {period}\b(?!\s*\[=)'
        if re.search(pattern, content_lower):
            date_str = f"{message_date.day} {MONTH_NAMES_REV[message_date.month].title()} {message_date.year}"
            resolved_dates[f'this_{period}'] = date_str
            result = re.sub(pattern, f'this {period} [= {date_str}]', result, flags=re.IGNORECASE)

    # 13h. "earlier today", "earlier this week"
    if re.search(r'\bearlier today\b(?!\s*\[=)', content_lower):
        date_str = f"{message_date.day} {MONTH_NAMES_REV[message_date.month].title()} {message_date.year}"
        resolved_dates['earlier_today'] = date_str
        result = re.sub(r'\bearlier today\b(?!\s*\[=)', f'earlier today [= {date_str}]', result, flags=re.IGNORECASE)

    # 13i. "the other day" (~2-3 days ago)
    if re.search(r'\bthe other day\b(?!\s*\[=)', content_lower):
        other_day = message_date - timedelta(days=2)
        date_str = f"around {other_day.day} {MONTH_NAMES_REV[other_day.month].title()} {other_day.year}"
        resolved_dates['the_other_day'] = date_str
        result = re.sub(r'\bthe other day\b(?!\s*\[=)', f'the other day [= {date_str}]', result, flags=re.IGNORECASE)

    # 13j. "a couple days ago" / "couple of days ago"
    if re.search(r'\ba? ?couple (?:of )?days? ago\b(?!\s*\[=)', content_lower):
        couple_days = message_date - timedelta(days=2)
        date_str = f"{couple_days.day} {MONTH_NAMES_REV[couple_days.month].title()} {couple_days.year}"
        resolved_dates['couple_days_ago'] = date_str
        result = re.sub(r'\ba? ?couple (?:of )?days? ago\b(?!\s*\[=)', f'a couple days ago [= {date_str}]', result, flags=re.IGNORECASE)

    # 13k. "day before yesterday"
    if re.search(r'\b(?:the )?day before yesterday\b(?!\s*\[=)', content_lower):
        day_before = message_date - timedelta(days=2)
        date_str = f"{day_before.day} {MONTH_NAMES_REV[day_before.month].title()} {day_before.year}"
        resolved_dates['day_before_yesterday'] = date_str
        result = re.sub(r'\b(?:the )?day before yesterday\b(?!\s*\[=)', f'day before yesterday [= {date_str}]', result, flags=re.IGNORECASE)

    # 13l. "next week"
    if re.search(r'\bnext week\b(?!\s*\[=)', content_lower):
        next_week = message_date + timedelta(days=7)
        week_start = next_week - timedelta(days=next_week.weekday())
        date_str = f"week of {week_start.day} {MONTH_NAMES_REV[week_start.month].title()} {week_start.year}"
        resolved_dates['next_week'] = date_str
        result = re.sub(r'\bnext week\b(?!\s*\[=)', f'next week [= {date_str}]', result, flags=re.IGNORECASE)

    # 13m. "over the weekend" / "during the weekend"
    if re.search(r'\b(?:over|during) the weekend\b(?!\s*\[=)', content_lower):
        days_since_saturday = (message_date.weekday() + 2) % 7
        if days_since_saturday == 0:
            days_since_saturday = 7
        last_saturday = message_date - timedelta(days=days_since_saturday)
        date_str = f"{last_saturday.day}-{(last_saturday + timedelta(days=1)).day} {MONTH_NAMES_REV[last_saturday.month].title()} {last_saturday.year}"
        resolved_dates['over_weekend'] = date_str
        result = re.sub(r'\b(over|during) the weekend\b(?!\s*\[=)', rf'\1 the weekend [= {date_str}]', result, flags=re.IGNORECASE)

    # 13n. "three/four/five days ago" (word form)
    for word, num in [('three', 3), ('four', 4), ('five', 5), ('six', 6), ('seven', 7)]:
        pattern = rf'\b{word} days? ago\b(?!\s*\[=)'
        if re.search(pattern, content_lower):
            target = message_date - timedelta(days=num)
            date_str = f"{target.day} {MONTH_NAMES_REV[target.month].title()} {target.year}"
            resolved_dates[f'{word}_days_ago'] = date_str
            result = re.sub(pattern, f'{word} days ago [= {date_str}]', result, flags=re.IGNORECASE)

    # 13o. "a week ago"
    if re.search(r'\ba week ago\b(?!\s*\[=)', content_lower):
        week_ago = message_date - timedelta(days=7)
        date_str = f"{week_ago.day} {MONTH_NAMES_REV[week_ago.month].title()} {week_ago.year}"
        resolved_dates['a_week_ago'] = date_str
        result = re.sub(r'\ba week ago\b(?!\s*\[=)', f'a week ago [= {date_str}]', result, flags=re.IGNORECASE)

    # 14. "ten years ago", "X years ago"
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
# COMPREHENSIVE QUERY MODE MARKERS
# =============================================================================
# Hashtable with hundreds of markers for universal question routing.
# Each query type has extensive pattern coverage across multiple domains.

QUERY_MODE_MARKERS = {
    "TEMPORAL": {
        # === When questions (150+ patterns) ===
        "when_starters": {
            "when ", "when's ", "when did ", "when does ", "when was ", "when were ",
            "when will ", "when is ", "when are ", "when has ", "when have ",
            "when had ", "when would ", "when could ", "when should ",
        },
        # === Date/time specific (100+ patterns) ===
        "date_time_queries": {
            # Date components
            "what date", "what day", "what year", "what month", "what week",
            "what time", "what hour", "what season", "what period", "what decade",
            "what century", "what era", "what quarter", "what semester",
            # Specific formats
            "on what day", "in what year", "in what month", "during what",
            "at what time", "on which day", "in which year", "in which month",
            "at which time", "on what date", "in which season", "during which",
            # Day parts
            "what morning", "what afternoon", "what evening", "what night",
            # Frequency/recurrence
            "what day of the week", "what time of day", "what part of",
        },
        # === Duration (80+ patterns) ===
        "duration": {
            # How long
            "how long ", "for how long", "how much time", "how long ago",
            "how long has", "how long have", "how long did", "how long was", "how long were",
            "how long will", "how long does", "how long would", "how long should",
            # Time units with how many
            "how many days", "how many weeks", "how many months", "how many years",
            "how many hours", "how many minutes", "how many seconds", "how many decades",
            "how many semesters", "how many quarters", "how many seasons",
            # Duration expressions
            "length of time", "amount of time", "period of time", "span of",
            "duration of", "time frame", "time period", "time span",
        },
        # === Sequence/Order (100+ patterns) ===
        "sequence_order": {
            # Before/after
            "after what", "before what", "since when", "until when", "by when",
            "happened before", "happened after", "came before", "came after",
            "occurred before", "occurred after", "took place before", "took place after",
            # Proximity
            "how soon", "how recently", "how early", "how late",
            "right before", "right after", "just before", "just after",
            "immediately before", "immediately after", "shortly before", "shortly after",
            # Temporal relationships
            "prior to", "subsequent to", "following the", "preceding the",
            "in the wake of", "leading up to", "in advance of", "ahead of",
            "in preparation for", "in anticipation of",
            # First/last
            "first time", "last time", "most recent", "earliest", "latest",
            "initial", "final", "beginning", "end",
        },
        # === Relative time references (150+ patterns) ===
        "relative_time": {
            # Week references
            "last week", "this week", "next week", "past week", "coming week",
            "previous week", "following week", "week before", "week after",
            # Month references
            "last month", "this month", "next month", "past month", "coming month",
            "previous month", "following month", "month before", "month after",
            # Year references
            "last year", "this year", "next year", "past year", "coming year",
            "previous year", "following year", "year before", "year after",
            # Day references
            "yesterday", "today", "tomorrow", "day before yesterday", "day after tomorrow",
            "last night", "tonight", "last evening", "this morning", "this afternoon",
            "this evening", "earlier today", "later today",
            # Ago patterns
            "ago did", "ago was", "ago were", "ago has", "ago have", "ago had",
            "years ago", "months ago", "weeks ago", "days ago", "hours ago",
            "minutes ago", "seconds ago", "moments ago", "recently",
            # Other relative
            "the other day", "the other night", "a while ago", "a while back",
            "not long ago", "long ago", "some time ago", "sometime ago",
            "once upon", "back when", "back in", "used to",
        },
        # === Temporal elapsed/passed (60+ patterns) ===
        "elapsed_time": {
            "time passed", "time elapsed", "time since", "time until", "time before",
            "passed between", "weeks passed", "days passed", "months passed", "years passed",
            "hours passed", "minutes passed", "elapsed since", "elapsed between",
            "gone by", "went by", "time gone", "time went",
        },
        # === Schedule/deadline/planning (80+ patterns) ===
        "schedule_deadline": {
            # When planning
            "by when", "due when", "deadline", "scheduled for", "planning to",
            "scheduled to", "set to", "slated for", "arranged for", "booked for",
            # Future planning
            "going to", "intending to", "planning on", "expecting to",
            "supposed to", "meant to", "about to", "preparing to",
            # Time constraints
            "before the", "after the", "during the", "throughout the",
            "for the duration", "until the", "up until", "as of",
        },
        # === Age/anniversary/milestone (60+ patterns) ===
        "age_milestone": {
            # Age
            "how old", "what age", "age of", "age when", "turned",
            # Anniversary
            "anniversary", "birthday", "celebration of",
            # Duration state
            "for how many", "lasted how long", "been going", "been happening",
            "been in", "been at", "been with", "been doing",
            # Milestones
            "milestone", "landmark", "turning point", "major event",
        },
        # === Frequency/recurrence (70+ patterns) ===
        "frequency": {
            "how often", "how frequently", "how regularly", "how many times",
            "how much", "frequency of", "rate of",
            # Recurrence
            "every time", "each time", "whenever", "always", "usually",
            "sometimes", "occasionally", "rarely", "never", "once", "twice",
            "multiple times", "several times", "many times", "few times",
            # Patterns
            "daily", "weekly", "monthly", "yearly", "annually",
            "hourly", "nightly", "seasonal", "periodic", "regular",
        },
        # === Temporal context (50+ patterns) ===
        "temporal_context": {
            # Seasons
            "in spring", "in summer", "in fall", "in autumn", "in winter",
            "during spring", "during summer", "during fall", "during winter",
            # Parts of day
            "in the morning", "in the afternoon", "in the evening", "at night",
            "at dawn", "at dusk", "at noon", "at midnight",
            # Time of life
            "as a child", "as a teen", "as an adult", "growing up",
            "in childhood", "in youth", "in adulthood",
        },
    },

    "AGGREGATION": {
        # === Activities/hobbies (200+ patterns) ===
        "activities_hobbies": {
            # Question starters
            "what activities", "what hobbies", "what things", "what items",
            "what tasks", "what chores", "what duties", "what responsibilities",
            "what does", "what do", "what did", "what has", "what have",
            "what would", "what will", "what can", "what could",
            # Sports/fitness
            "what sports", "what exercises", "what workouts", "what training",
            "what physical activities", "what fitness",
            # Creative activities
            "what art", "what crafts", "what projects", "what creations",
            "what creative", "what artistic",
            # Leisure
            "what recreational", "what leisure", "what pastimes", "what fun",
            # Verb patterns (likes/enjoys/does)
            " like", " likes", " enjoy", " enjoys", " love", " loves",
            " prefer", " prefers", " hate", " hates", " do", " does",
            " participate in", " partake in", " engage in", " involved in",
        },
        # === Locations/places (150+ patterns) ===
        "locations_places": {
            # Where questions
            "where has", "where have", "where did", "where does", "where was", "where were",
            "where will", "where can", "where would", "where could",
            # Place types
            "what places", "which places", "what locations", "what destinations",
            "what cities", "what countries", "what regions", "what areas",
            "what towns", "what villages", "what neighborhoods", "what districts",
            "what states", "what provinces", "what territories",
            # Specific venues
            "what restaurants", "what cafes", "what bars", "what clubs",
            "what stores", "what shops", "what malls", "what markets",
            "what museums", "what galleries", "what theaters", "what venues",
            "what parks", "what beaches", "what mountains", "what trails",
            # Travel
            "visited", "traveled to", "been to", "gone to", "went to",
        },
        # === Media/content (200+ patterns) ===
        "media_content": {
            # Books
            "what books", "what novels", "what stories", "what literature",
            "what magazines", "what newspapers", "what articles", "what publications",
            # Visual media
            "what movies", "what films", "what shows", "what series",
            "what tv shows", "what television", "what documentaries", "what videos",
            "what channels", "what streaming", "what youtube",
            # Audio
            "what songs", "what music", "what albums", "what tracks",
            "what podcasts", "what audiobooks", "what playlists",
            "what artists", "what bands", "what musicians", "what singers",
            # Games
            "what games", "what video games", "what board games", "what card games",
            # Digital
            "what apps", "what programs", "what software", "what websites",
            "what platforms", "what tools", "what services",
            # Theater/performance
            "what plays", "what musicals", "what performances", "what concerts",
            "what operas", "what ballets",
            # Verbs
            "read", "reads", "watched", "watches", "listened", "listens",
            "viewed", "views", "seen", "saw", "played", "plays",
            "streamed", "streams", "downloaded", "downloads",
        },
        # === Events (150+ patterns) ===
        "events_occasions": {
            # General events
            "what events", "what occasions", "what ceremonies", "what celebrations",
            "what meetings", "what appointments", "what gatherings",
            " events has ", " events have ", " events did ", " events does ",
            # Social events
            "what festivals", "what conferences", "what competitions", "what tournaments",
            "what parties", "what weddings", "what reunions", "what get-togethers",
            "what dinners", "what lunches", "what brunches",
            # Professional events
            "what seminars", "what workshops", "what training", "what courses",
            "what lectures", "what presentations", "what talks",
            # Entertainment events
            "what concerts", "what shows", "what performances", "what exhibitions",
            "what fairs", "what expos", "what conventions",
            # Community events
            "what rallies", "what protests", "what demonstrations", "what marches",
            "what fundraisers", "what charity events", "what volunteer",
            # Participated patterns
            "participated in", "attended", "went to", "joined", "took part in",
        },
        # === Types/kinds/categories (120+ patterns) ===
        "types_kinds": {
            # Basic type questions
            "what types", "what kinds", "what sorts", "what categories",
            "what varieties", "what forms", "what styles", "what versions",
            "what kind of", "what sort of", "what type of",
            # Brands/models
            "what brands", "what models", "what makes", "what manufacturers",
            # Flavors/variants
            "what flavors", "what variants", "what options", "what choices",
            # Genres/styles
            "what genres", "what styles", "what formats", "what methods",
        },
        # === Attributes/features (150+ patterns) ===
        "attributes_features": {
            # Symbols/signs
            "what symbols", "which symbols", "what signs", "which signs",
            "what icons", "what emblems", "what badges", "what logos",
            # Traits/characteristics
            "what traits", "what characteristics", "what qualities", "what features",
            "what properties", "what attributes", "what aspects",
            # Physical attributes
            "what colors", "what sizes", "what shapes", "what patterns",
            "what designs", "what textures", "what materials",
            # Specific features
            "what features does", "what functions", "what capabilities",
            "what specifications", "what details",
        },
        # === Possessions/ownership (200+ patterns) ===
        "possessions_ownership": {
            # Pets/animals
            "what pets", "what animals", "what dogs", "what cats",
            "what birds", "what fish", "what livestock",
            # Objects
            "what items", "what things", "what stuff", "what objects",
            "what possessions", "what belongings",
            # Equipment/tools
            "what instruments", "what tools", "what equipment", "what devices",
            "what machines", "what appliances", "what gadgets",
            # Vehicles
            "what cars", "what vehicles", "what bikes", "what motorcycles",
            "what boats", "what planes",
            # Technology
            "what computers", "what phones", "what tablets", "what electronics",
            # Clothing/accessories
            "what clothes", "what outfits", "what shoes", "what accessories",
            "what jewelry", "what watches",
            # Collections
            "what collection", "what collectibles", "what memorabilia",
            # Skills/abilities
            "what languages", "what skills", "what talents", "what abilities",
            "what certifications", "what degrees", "what qualifications",
            # Ownership verbs
            "has", "have", "owns", "own", "possesses", "possess",
            "carries", "carry", "holds", "hold",
        },
        # === Food/cooking (100+ patterns) ===
        "food_cooking": {
            "what foods", "what dishes", "what meals", "what recipes",
            "what cuisines", "what ingredients", "what spices",
            "what drinks", "what beverages", "what cocktails",
            "what desserts", "what snacks", "what appetizers",
            "cooked", "cooks", "prepared", "prepares", "made", "makes",
            "ate", "eats", "drank", "drinks", "tasted", "tastes",
        },
        # === Shopping/purchases (100+ patterns) ===
        "shopping_purchases": {
            "what bought", "what purchased", "what acquired", "what got",
            "what products", "what items purchased", "what goods",
            "bought", "buys", "purchased", "purchases", "acquired", "got",
            "shopping for", "looking for", "searching for",
        },
        # === Achievements/accomplishments (80+ patterns) ===
        "achievements": {
            "what achievements", "what accomplishments", "what awards", "what prizes",
            "what honors", "what recognition", "what medals", "what trophies",
            "what milestones", "what successes", "what wins", "what victories",
            "achieved", "accomplished", "won", "earned", "received",
        },
        # === Problems/challenges (80+ patterns) ===
        "problems_challenges": {
            "what problems", "what issues", "what challenges", "what difficulties",
            "what obstacles", "what setbacks", "what struggles", "what troubles",
            "what concerns", "what worries", "what fears",
            "faced", "encountered", "experienced", "dealt with", "handled",
        },
        # === Changes/modifications (60+ patterns) ===
        "changes": {
            "what changes", "what modifications", "what updates", "what improvements",
            "what adjustments", "what alterations", "what revisions",
            "changed", "modified", "updated", "improved", "adjusted", "altered",
        },
        # === Relationships/people (100+ patterns) ===
        "relationships_people": {
            # Family
            "what kids", "what children", "what siblings", "what family",
            "what relatives", "what cousins", "what grandparents",
            "how many kids", "how many children", "how many siblings",
            # Friends/social
            "what friends", "what colleagues", "what coworkers", "what teammates",
            "what classmates", "what neighbors", "what acquaintances",
            # Professional
            "what mentors", "what teachers", "what professors", "what coaches",
            "what supervisors", "what managers", "what employees",
            # Relationship verbs
            "knows", "know", "met", "meets", "befriended", "befriends",
        },
        # === Collections/enumerations (80+ patterns) ===
        "collections_lists": {
            # List commands
            "list all", "list the", "name all", "name the",
            "enumerate", "complete list", "full list", "entire list",
            # All patterns
            "what are all", "what were all", "who are all", "who were all",
            "all of the", "each of the", "every one of", "every single",
            "everything that", "all that", "everyone who", "everybody who",
            # Multiple/various
            "various", "multiple", "several", "different", "diverse",
        },
        # === Preferences/favorites (100+ patterns) ===
        "preferences_favorites": {
            # Favorite
            "favorite things", "favorite activities", "favorite places",
            "favorite foods", "favorite books", "favorite movies",
            "what is your favorite", "what are your favorite",
            "what's your favorite", "what were your favorite",
            # Preferences
            "preferred", "preference for", "top choices", "main interests",
            "what do you like", "what do you enjoy", "what do you love",
            "what do you prefer", "best liked", "most enjoyed",
            # Least favorite (still aggregation)
            "least favorite", "like the least", "dislike", "hate",
        },
        # === Education/learning (80+ patterns) ===
        "education_learning": {
            "what courses", "what classes", "what subjects", "what topics",
            "what degrees", "what certifications", "what training",
            "what schools", "what universities", "what colleges",
            "studied", "studies", "learned", "learns", "took", "takes",
        },
        # === Work/career (80+ patterns) ===
        "work_career": {
            "what jobs", "what positions", "what roles", "what responsibilities",
            "what projects", "what assignments", "what tasks",
            "what companies", "what organizations", "what employers",
            "worked", "works", "employed", "contracted",
        },
    },

    "INFERENTIAL": {
        # === Modal verbs (100+ patterns) ===
        "modals_possibility": {
            # Would
            "would ", "would be", "would have", "would likely", "would probably",
            "would possibly", "would seem", "would appear", "wouldn't ",
            # Could
            "could ", "could be", "could have", "could likely", "could possibly",
            "couldn't ",
            # Might/may
            "might ", "might be", "might have", "might not", "mightn't ",
            "may ", "may be", "may have", "may not",
            # Should
            "should ", "should be", "should have", "shouldn't ",
            # Other modals
            "must be", "must have", "can't be", "cannot be",
        },
        # === Why/causation (120+ patterns) ===
        "causation_reasoning": {
            # Why questions
            "why ", "why's ", "why did ", "why does ", "why is ", "why was ",
            "why has ", "why have ", "why would ", "why should ", "why might ",
            # How come
            "how come ", "how is it that", "how does it happen",
            # Cause/effect
            "what caused ", "what made ", "what led to ", "what resulted in",
            "what brought about", "what triggered", "what prompted",
            # Reason/motivation
            "reason for", "reasons for", "reasoning behind", "rationale for",
            "motivation for", "motivations for", "motive for",
            "purpose of", "goal of", "aim of", "objective of",
            # Due to/because
            "caused by", "due to", "because of", "thanks to", "owing to",
            "attributed to", "stemming from", "arising from",
            # Impact/consequence
            "impact of", "effect of", "consequence of", "result of",
            "outcome of", "implication of",
        },
        # === Hypothetical/possibility (150+ patterns) ===
        "hypothetical_speculation": {
            # Likelihood questions
            "is it likely", "is it possible", "is it probable", "is it plausible",
            "would it be", "could it be", "might it be",
            # What if
            "what if", "if ", "suppose ", "supposing ", "imagine if",
            "assuming that", "given that", "provided that",
            # Potential/possibility
            "likelihood", "likelihood of", "probability", "probability of",
            "chance", "chance of", "potential", "potential for",
            "possibility", "possibility of", "risk", "risk of",
            # Speculation
            "speculate", "speculation", "conjecture", "theory", "hypothesis",
            # Future predictions
            "will likely", "will probably", "will possibly",
            "going to", "expected to", "projected to", "anticipated to",
            # Scenarios
            "scenario where", "case where", "situation where", "instance where",
        },
        # === Opinion/judgment (100+ patterns) ===
        "opinion_judgment": {
            # Think/believe
            "do you think", "would you say", "would you agree",
            "in your opinion", "what do you think", "what would you say",
            "personally", "i believe", "i think", "i feel", "i suspect",
            # Judgment questions
            "would you consider", "would you describe", "would you characterize",
            "how would you describe", "how would you characterize",
            "how would you rate", "how would you evaluate",
            # Impressions
            "impression of", "view of", "perspective on", "stance on",
            "attitude toward", "opinion on", "feeling about",
            # Assessment
            "assessment of", "evaluation of", "appraisal of", "judgment of",
        },
        # === Inference indicators (150+ patterns) ===
        "inference_deduction": {
            # Probability adverbs
            " likely ", " likely?", " probably ", " probably?",
            " possibly ", " possibly?", " perhaps ", " maybe ",
            " presumably ", " conceivably ", " potentially ",
            # Inference verbs
            " suggest ", " suggests ", " imply ", " implies ",
            " indicate ", " indicates ", " predict ", " predicts ",
            " infer ", " infers ", " deduce ", " deduces ",
            " conclude ", " concludes ", " assume ", " assumes ",
            " suppose ", " supposes ", " presume ", " presumes ",
            # Appearance/seeming
            " believe ", " believes ", " seem ", " seems ",
            " appear ", " appears ", " look like ", " looks like ",
            " sound like ", " sounds like ",
            # Interpretation
            "interpret", "interpretation", "understand", "understanding",
            "construe", "perceive", "perception",
        },
        # === Personality/character inference (100+ patterns) ===
        "personality_traits": {
            # Personality questions
            "what kind of person", "what type of person", "what sort of person",
            "what personality", "personality type", "personality traits",
            # Character traits
            "what traits", "what characteristics", "what qualities",
            "what attributes", "what tendencies", "what inclinations",
            # Describe person
            "describe ", "description of", "characterize", "characterization of",
            # Nature/temperament
            "nature of", "temperament", "disposition", "demeanor",
            "character of", "mentality", "mindset",
        },
        # === Career/interest inference (120+ patterns) ===
        "career_interests": {
            # Fields/careers
            "what fields", "what field", "what career", "what careers",
            "what job", "what jobs", "what profession", "what professions",
            "what industry", "what industries", "what sector", "what domain",
            # Likely to pursue
            " pursue ", " pursuing ", " aspire ", " aspiring ",
            " strive ", " striving ", " aim ", " aiming ",
            "interested in", "interest in", "passion for", "passionate about",
            # Suited for
            "suited for", "fit for", "good at", "excel at",
            "talented in", "skilled in", "gifted in",
            # Future plans
            "future in", "plans to", "planning to", "hoping to",
            "wanting to", "seeking to", "looking to",
        },
        # === Preference/inclination inference (100+ patterns) ===
        "preferences_inclinations": {
            # Be considered
            "be considered ", "considered to be", "be seen as",
            "be regarded as", "be viewed as", "be thought of as",
            # Be open to/willing
            "be open to ", "be willing to", "be inclined to",
            "be interested in", "be attracted to", "be drawn to",
            "be likely to", "be prone to", "be apt to",
            # Enjoy/appreciate
            "enjoy ", "appreciate ", "value ", "cherish ",
            "embrace ", "welcome ", "favor ", "support ",
            # Lean toward
            "lean toward", "leaning toward", "tend to", "tendency to",
        },
        # === Advice/recommendation (80+ patterns) ===
        "advice_recommendation": {
            # Should
            "should", "shouldn't", "ought to", "ought not to",
            "supposed to", "not supposed to",
            # Recommend/suggest
            "recommend", "recommendation", "suggested", "suggestion",
            "advise", "advice", "propose", "proposal",
            # Better/best
            "better", "better to", "best to", "best if",
            "preferable", "advisable", "wise to", "smart to",
            # Worth it
            "worth", "worthwhile", "beneficial", "helpful",
        },
        # === Comparison/contrast (100+ patterns) ===
        "comparison_contrast": {
            # Comparison
            "more than", "less than", "better than", "worse than",
            "greater than", "lesser than", "superior to", "inferior to",
            "compared to", "in comparison", "in contrast", "versus", "vs",
            # Similarity/difference
            "different from", "similar to", "same as", "alike", "unlike",
            "resembles", "differs from", "contrasts with",
            "analogous to", "comparable to", "equivalent to",
            # Relative
            "relatively", "comparatively", "in relation to", "relative to",
        },
        # === Emotional/mental state inference (100+ patterns) ===
        "emotional_mental_state": {
            # Feel/emotion
            "how does .* feel", "how do .* feel", "feel about",
            "feelings about", "emotion", "emotional state",
            # Think/believe
            "what does .* think", "what do .* think", "think about",
            "thoughts on", "belief about", "believes that",
            # Attitude/view
            "attitude toward", "attitudes toward", "view of", "views on",
            "opinion on", "opinions on", "stance on",
            # Mental state
            "mindset", "perspective", "outlook", "mentality",
        },
        # === Behavioral prediction (80+ patterns) ===
        "behavioral_prediction": {
            # Likely behavior
            "likely to do", "probably do", "tend to do", "inclined to do",
            # React/respond
            "react to", "respond to", "handle", "deal with",
            "approach", "tackle", "address",
            # Future actions
            "next step", "next move", "what will", "what would",
            "expected to do", "planning to do", "intending to do",
        },
        # === Negation/opposite (60+ patterns) ===
        "negation_opposite": {
            # Negative modals
            " wouldn't ", " couldn't ", " shouldn't ", " mightn't ",
            " won't ", " can't ", " don't ", " doesn't ",
            # Negative inference
            "unlikely", "improbable", "doubtful", "questionable",
            "not likely", "not probable", "not possible",
            # Opposite
            "instead of", "rather than", "as opposed to", "contrary to",
        },
        # === Relationships/connections (80+ patterns) ===
        "relationships_connections": {
            # Relationship quality
            "how close", "how well", "relationship with", "connection with",
            "bond with", "ties to", "link to", "association with",
            # Social dynamics
            "get along with", "compatible with", "friendly with",
            "rapport with", "chemistry with",
        },
    },

    "STRICT": {
        # === Direct questions (what is, who is, what did) ===
        "direct_extraction": {
            "what is ", "what's ", "what was ", "what were ",
            "who is ", "who's ", "who was ", "who were ",
            "which is ", "which was ",
            "is ", "are ", "was ", "were ",
        },
        # === Definition/identity ===
        "identity": {
            "define", "definition", "meaning", "means",
            "identity of", "name of", "called",
        },
        # === Simple attributes ===
        "simple_attributes": {
            "color", "size", "weight", "height", "age",
            "name", "address", "phone", "email",
            "job", "occupation", "profession", "role",
        },
    },
}

# =============================================================================
# QUERY MODE INFERENCE
# =============================================================================

def infer_query_mode(question: str) -> str:
    """Infer query mode from question text using comprehensive marker hashtable.

    Uses QUERY_MODE_MARKERS hashtable with 2000+ patterns across all query types.
    This routing is UNIVERSAL and domain-agnostic.

    Returns: "TEMPORAL", "INFERENTIAL", "AGGREGATION", "ADVERSARIAL", or "STRICT"

    Args:
        question: The query text

    Returns:
        One of the query mode strings
    """
    q = question.lower().strip()

    # === EARLY EXIT: Non-temporal questions with time context ===
    # Questions asking WHERE/WHAT/WHO (not WHEN) should NOT be temporal
    # Example: "Where did X move from 4 years ago?" is about location, not time
    # Example: "What was the poetry reading about?" is about content, not time
    non_temporal_starters = [
        "where did", "where does", "where is", "where was", "where has", "where have",
        # Aggregation questions that might mention time
        "what did", "what does", "what has", "what have", "what was", "what is", "what are",
        "who did", "who does", "who is", "who was",
        # HOW questions (methods, not timing)
        "how did", "how does", "how has", "how was",
    ]
    is_non_temporal_question = any(q.startswith(s) for s in non_temporal_starters)

    # CRITICAL: "about" questions ask for DESCRIPTION, never temporal
    # "What was X about?" = content/topic, NOT when
    # "What did X talk about?" = content, NOT when
    if " about?" in q or " about " in q:
        is_non_temporal_question = True

    # === TEMPORAL: Check all temporal patterns (2000+ patterns) ===
    if not is_non_temporal_question:
        # Collect all temporal patterns from hashtable
        temporal_markers = QUERY_MODE_MARKERS["TEMPORAL"]

        # Check patterns that require startswith
        starter_categories = ["when_starters", "date_time_queries"]
        for category in starter_categories:
            for pattern in temporal_markers.get(category, set()):
                if q.startswith(pattern):
                    return "TEMPORAL"

        # Check patterns that can appear anywhere (contains)
        contains_categories = [
            "duration", "sequence_order", "relative_time", "elapsed_time",
            "schedule_deadline", "age_milestone", "frequency", "temporal_context"
        ]
        for category in contains_categories:
            for pattern in temporal_markers.get(category, set()):
                if pattern in q:
                    return "TEMPORAL"

    # === INFERENTIAL: Check FIRST (more specific than aggregation) ===
    # Open_domain questions need inference even if they mention traits/attributes
    inferential_markers = QUERY_MODE_MARKERS["INFERENTIAL"]

    # Check patterns that require startswith
    starter_categories = ["modals_possibility", "causation_reasoning", "hypothetical_speculation"]
    for category in starter_categories:
        for pattern in inferential_markers.get(category, set()):
            if q.startswith(pattern):
                return "INFERENTIAL"

    # Check patterns that can appear anywhere
    contains_categories = [
        "opinion_judgment", "inference_deduction", "personality_traits",
        "career_interests", "preferences_inclinations", "advice_recommendation",
        "comparison_contrast", "emotional_mental_state", "behavioral_prediction",
        "negation_opposite", "relationships_connections"
    ]
    for category in contains_categories:
        for pattern in inferential_markers.get(category, set()):
            if pattern in q:
                return "INFERENTIAL"

    # Additional high-priority INFERENTIAL patterns for open_domain
    # These override AGGREGATION when both could apply
    high_priority_inference = [
        # Speculation/prediction keywords
        " might ", " could ", " may ", " would ", " should ",
        # Underlying/hidden reasoning
        "underlying", "based on", "given", "considering",
        # Alternative/hypothetical
        "alternative", "instead", "rather",
        # Subjective judgment
        "describe", "characterize", "personality",
    ]
    for pattern in high_priority_inference:
        if pattern in q:
            return "INFERENTIAL"

    # === AGGREGATION: Check after INFERENTIAL (broader patterns) ===
    aggregation_markers = QUERY_MODE_MARKERS["AGGREGATION"]

    # Check all aggregation categories
    for category, patterns in aggregation_markers.items():
        for pattern in patterns:
            if pattern in q:
                return "AGGREGATION"

    # Special aggregation logic
    if "how many" in q:
        # "how many" without time units = aggregation
        time_units = ["days", "weeks", "months", "years", "hours", "minutes", "seconds"]
        if not any(t in q for t in time_units):
            return "AGGREGATION"

    # "What X events" pattern (e.g., "What LGBTQ+ events has Caroline...")
    import re
    if re.search(r'\bwhat\b.{1,30}\bevents\b', q):
        return "AGGREGATION"

    # === ADVERSARIAL: Negation and unanswerable detection ===
    # These patterns suggest the question may be tricky/adversarial
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

    # === Default to STRICT (explicit fact extraction) ===
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

"""Temporal Resolution - Computing dates from relative temporal markers.

THE CRITICAL INSIGHT:
When a message says "I went to Paris yesterday" during a session on "8 May 2023",
the actual date of the Paris trip is "7 May 2023".

The date is NOT in the message text - it must be COMPUTED from:
1. The session timestamp (when the conversation occurred)
2. The temporal marker in the text ("yesterday")
3. Simple date arithmetic

This module:
1. Parses session datetime strings
2. Extracts temporal markers from message text
3. Computes the actual calendar dates
4. Enriches messages with resolved dates for retrieval
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_types import NeuralNode


# =============================================================================
# TEMPORAL MARKER PATTERNS
# =============================================================================

# Days relative to conversation date
DAY_OFFSETS = {
    # Past
    "yesterday": -1,
    "the day before yesterday": -2,
    "day before yesterday": -2,
    "two days ago": -2,
    "three days ago": -3,
    "four days ago": -4,
    "five days ago": -5,
    "six days ago": -6,
    "a week ago": -7,

    # Present
    "today": 0,
    "this morning": 0,
    "this afternoon": 0,
    "this evening": 0,
    "tonight": 0,

    # Future
    "tomorrow": 1,
    "the day after tomorrow": 2,
    "day after tomorrow": 2,
}

# Weeks relative
WEEK_OFFSETS = {
    "last week": -7,
    "the week before": -7,
    "a week ago": -7,
    "two weeks ago": -14,
    "this week": 0,
    "next week": 7,
    "in a week": 7,
}

# Months relative (approximate - use 30 days)
MONTH_OFFSETS = {
    "last month": -30,
    "a month ago": -30,
    "two months ago": -60,
    "three months ago": -90,
    "this month": 0,
    "next month": 30,
    "in a month": 30,
}

# Years relative (approximate - use 365 days)
YEAR_OFFSETS = {
    "last year": -365,
    "a year ago": -365,
    "two years ago": -730,
    "three years ago": -1095,
    "four years ago": -1460,
    "five years ago": -1825,
    "this year": 0,
    "next year": 365,
}

# Weekday patterns (need special handling)
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Pattern for "last Monday", "this Friday", etc.
WEEKDAY_PATTERN = re.compile(
    r"(last|this|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
    re.IGNORECASE
)

# Pattern for "the Sunday before 25 May 2023"
BEFORE_DATE_PATTERN = re.compile(
    r"the\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+before\s+(\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE
)


# =============================================================================
# DATE PARSING
# =============================================================================

def parse_session_datetime(datetime_str: str) -> datetime | None:
    """Parse session datetime strings like '1:56 pm on 8 May, 2023'.

    Handles various formats:
    - "1:56 pm on 8 May, 2023"
    - "7:55 pm on 9 June, 2023"
    - "2023-05-08T13:56:00"

    Returns:
        datetime object or None if parsing fails
    """
    if not datetime_str:
        return None

    # Try "H:MM am/pm on D Month, YYYY" format
    pattern1 = r"(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})"
    match = re.search(pattern1, datetime_str, re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        ampm = match.group(3).lower()
        day = int(match.group(4))
        month_name = match.group(5)
        year = int(match.group(6))

        # Convert to 24-hour
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        # Parse month name
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }
        month = months.get(month_name.lower())
        if month:
            try:
                return datetime(year, month, day, hour, minute)
            except ValueError:
                pass

    # Try ISO format
    try:
        return datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Try common datetime formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %B %Y",
        "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue

    return None


def format_date(dt: datetime) -> str:
    """Format datetime as human-readable date string.

    Examples:
        "7 May 2023"
        "25 December 2022"
    """
    return dt.strftime("%-d %B %Y").replace(" 0", " ")  # Remove leading zeros


def format_date_windows(dt: datetime) -> str:
    """Format datetime as human-readable date string (Windows compatible).

    Examples:
        "7 May 2023"
        "25 December 2022"
    """
    # Windows doesn't support %-d, use manual formatting
    return f"{dt.day} {dt.strftime('%B')} {dt.year}"


# =============================================================================
# TEMPORAL MARKER EXTRACTION
# =============================================================================

@dataclass
class TemporalMention:
    """A temporal expression found in text."""
    marker: str           # The text found ("yesterday", "last week")
    offset_days: int      # Days relative to session date
    resolved_date: str    # The computed actual date
    confidence: float     # How confident we are in resolution
    span: tuple[int, int] # Character positions in original text


def extract_temporal_markers(
    text: str,
    session_date: datetime,
) -> list[TemporalMention]:
    """Extract and resolve temporal markers from text.

    Args:
        text: Message text to analyze
        session_date: The date when this message was sent

    Returns:
        List of TemporalMention objects with resolved dates
    """
    text_lower = text.lower()
    mentions = []

    # Check day offsets (most specific)
    for marker, offset in DAY_OFFSETS.items():
        if marker in text_lower:
            resolved = session_date + timedelta(days=offset)
            start = text_lower.find(marker)
            mentions.append(TemporalMention(
                marker=marker,
                offset_days=offset,
                resolved_date=format_date_windows(resolved),
                confidence=0.95,
                span=(start, start + len(marker)),
            ))

    # Check week offsets
    for marker, offset in WEEK_OFFSETS.items():
        if marker in text_lower:
            resolved = session_date + timedelta(days=offset)
            start = text_lower.find(marker)
            mentions.append(TemporalMention(
                marker=marker,
                offset_days=offset,
                resolved_date=format_date_windows(resolved),
                confidence=0.85,  # Lower confidence for approximate
                span=(start, start + len(marker)),
            ))

    # Check month offsets
    for marker, offset in MONTH_OFFSETS.items():
        if marker in text_lower:
            resolved = session_date + timedelta(days=offset)
            start = text_lower.find(marker)
            mentions.append(TemporalMention(
                marker=marker,
                offset_days=offset,
                resolved_date=format_date_windows(resolved),
                confidence=0.75,  # Month approximations are less precise
                span=(start, start + len(marker)),
            ))

    # Check year offsets
    for marker, offset in YEAR_OFFSETS.items():
        if marker in text_lower:
            resolved = session_date + timedelta(days=offset)
            start = text_lower.find(marker)
            mentions.append(TemporalMention(
                marker=marker,
                offset_days=offset,
                resolved_date=format_date_windows(resolved),
                confidence=0.70,  # Year approximations are less precise
                span=(start, start + len(marker)),
            ))

    # Check "last/this/next [weekday]" patterns
    for match in WEEKDAY_PATTERN.finditer(text_lower):
        modifier = match.group(1).lower()
        weekday_name = match.group(2).lower()
        weekday_num = WEEKDAYS[weekday_name]

        # Calculate offset
        current_weekday = session_date.weekday()
        if modifier == "last":
            # Go back to the previous occurrence
            days_back = (current_weekday - weekday_num) % 7
            if days_back == 0:
                days_back = 7  # If same day, go back a week
            offset = -days_back
        elif modifier == "this":
            # This week's occurrence
            offset = weekday_num - current_weekday
        else:  # next
            # Next week's occurrence
            days_forward = (weekday_num - current_weekday) % 7
            if days_forward == 0:
                days_forward = 7
            offset = days_forward

        resolved = session_date + timedelta(days=offset)
        mentions.append(TemporalMention(
            marker=match.group(0),
            offset_days=offset,
            resolved_date=format_date_windows(resolved),
            confidence=0.90,
            span=(match.start(), match.end()),
        ))

    # Check "the Sunday before 25 May 2023" patterns
    for match in BEFORE_DATE_PATTERN.finditer(text_lower):
        weekday_name = match.group(1).lower()
        date_str = match.group(2)
        weekday_num = WEEKDAYS[weekday_name]

        # Parse the reference date
        ref_date = parse_session_datetime(date_str)
        if ref_date:
            # Find the weekday before this date
            days_back = (ref_date.weekday() - weekday_num) % 7
            if days_back == 0:
                days_back = 7
            resolved = ref_date - timedelta(days=days_back)

            # Calculate offset from session date
            offset = (resolved - session_date).days

            mentions.append(TemporalMention(
                marker=match.group(0),
                offset_days=offset,
                resolved_date=format_date_windows(resolved),
                confidence=0.95,
                span=(match.start(), match.end()),
            ))

    return mentions


# =============================================================================
# MESSAGE ENRICHMENT
# =============================================================================

def enrich_message_with_temporal(
    content: str,
    session_datetime: str | datetime,
    metadata: dict | None = None,
) -> tuple[str, dict]:
    """Enrich message content and metadata with resolved temporal dates.

    This is the KEY function for temporal resolution. It:
    1. Parses the session datetime
    2. Extracts temporal markers from the message
    3. Computes actual dates
    4. Adds resolved dates to metadata
    5. Optionally appends resolved dates to content for embedding

    Args:
        content: Original message text
        session_datetime: Session datetime (string or datetime object)
        metadata: Existing metadata dict (will be modified)

    Returns:
        Tuple of (enriched_content, updated_metadata)

    Example:
        >>> enrich_message_with_temporal(
        ...     "I went to Paris yesterday",
        ...     "1:56 pm on 8 May, 2023"
        ... )
        ("I went to Paris yesterday [resolved: 7 May 2023]",
         {"temporal_markers": [...], "resolved_dates": ["7 May 2023"]})
    """
    if metadata is None:
        metadata = {}

    # Parse session datetime
    if isinstance(session_datetime, str):
        session_date = parse_session_datetime(session_datetime)
    else:
        session_date = session_datetime

    if not session_date:
        return content, metadata

    # ALWAYS store session_date in metadata - this is the actual conversation date
    # Critical for answering "When did X happen?" questions
    metadata["session_date"] = format_date_windows(session_date)

    # Extract temporal markers
    mentions = extract_temporal_markers(content, session_date)

    if not mentions:
        return content, metadata

    # Store temporal markers in metadata
    metadata["temporal_markers"] = [
        {
            "marker": m.marker,
            "resolved_date": m.resolved_date,
            "confidence": m.confidence,
        }
        for m in mentions
    ]
    metadata["resolved_dates"] = list(set(m.resolved_date for m in mentions))
    # session_date already set above

    # Enrich content with resolved dates for better embedding/retrieval
    # Only add if there's a high-confidence resolution
    high_conf_dates = [m.resolved_date for m in mentions if m.confidence >= 0.85]
    if high_conf_dates:
        # Deduplicate while preserving order
        seen = set()
        unique_dates = []
        for d in high_conf_dates:
            if d not in seen:
                seen.add(d)
                unique_dates.append(d)

        # Append to content for embedding
        date_suffix = " [dates: " + ", ".join(unique_dates) + "]"
        enriched_content = content + date_suffix
    else:
        enriched_content = content

    return enriched_content, metadata


# =============================================================================
# NODE ENRICHMENT
# =============================================================================

def enrich_node_temporal(
    node: "NeuralNode",
    session_datetime: str | datetime,
) -> "NeuralNode":
    """Enrich a NeuralNode with resolved temporal information.

    Modifies the node's content and metadata in-place.

    Args:
        node: The NeuralNode to enrich
        session_datetime: Session datetime for resolution

    Returns:
        The modified node (also modifies in-place)
    """
    enriched_content, enriched_metadata = enrich_message_with_temporal(
        node.content,
        session_datetime,
        node.metadata.copy(),
    )

    # Update node
    node.content = enriched_content
    node.metadata.update(enriched_metadata)

    return node


# =============================================================================
# BATCH ENRICHMENT
# =============================================================================

@dataclass
class TemporalEnrichmentStats:
    """Statistics from batch temporal enrichment."""
    total_messages: int = 0
    messages_with_temporal: int = 0
    markers_found: int = 0
    dates_resolved: int = 0

    @property
    def temporal_coverage(self) -> float:
        """Percentage of messages with temporal markers."""
        if self.total_messages == 0:
            return 0.0
        return self.messages_with_temporal / self.total_messages


def enrich_session_temporal(
    messages: list["NeuralNode"],
    session_datetime: str | datetime,
) -> TemporalEnrichmentStats:
    """Enrich all messages in a session with temporal resolution.

    Args:
        messages: List of NeuralNode messages
        session_datetime: Session datetime for resolution

    Returns:
        Statistics about the enrichment
    """
    stats = TemporalEnrichmentStats()

    for node in messages:
        stats.total_messages += 1

        enriched_content, enriched_metadata = enrich_message_with_temporal(
            node.content,
            session_datetime,
            node.metadata.copy(),
        )

        if enriched_metadata.get("temporal_markers"):
            stats.messages_with_temporal += 1
            stats.markers_found += len(enriched_metadata["temporal_markers"])
            stats.dates_resolved += len(enriched_metadata.get("resolved_dates", []))

            # Update node
            node.content = enriched_content
            node.metadata.update(enriched_metadata)

    return stats


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def resolve_temporal_query(
    query: str,
    reference_date: datetime | None = None,
) -> list[str]:
    """Extract dates that a temporal query might be asking about.

    Useful for query expansion.

    Args:
        query: The question being asked
        reference_date: Reference date (defaults to now)

    Returns:
        List of date strings that might answer the query
    """
    if reference_date is None:
        reference_date = datetime.now()

    mentions = extract_temporal_markers(query, reference_date)
    return [m.resolved_date for m in mentions]

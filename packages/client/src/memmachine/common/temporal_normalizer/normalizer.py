"""Temporal normalization for converting relative dates to absolute dates.

Handles temporal questions by:
1. Extracting dates from episode context (session timestamps)
2. Resolving relative references ("yesterday", "last week")
3. Formatting dates consistently for LLM consumption
"""

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Month name mappings
MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

WEEKDAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


@dataclass
class NormalizedDate:
    """A normalized date with confidence."""

    year: int | None = None
    month: int | None = None
    day: int | None = None
    confidence: float = 0.0
    source_text: str = ""
    is_relative: bool = False

    @property
    def is_complete(self) -> bool:
        """Whether this is a complete date (has day, month, year)."""
        return self.year is not None and self.month is not None and self.day is not None

    @property
    def has_year(self) -> bool:
        return self.year is not None

    @property
    def has_month(self) -> bool:
        return self.month is not None

    def to_string(self, include_day: bool = True) -> str:
        """Convert to readable string."""
        if self.is_complete and include_day:
            try:
                dt = datetime(self.year, self.month, self.day)
                return dt.strftime("%d %B %Y")
            except ValueError:
                pass

        if self.has_year and self.has_month:
            month_name = list(MONTH_NAMES.keys())[self.month - 1].capitalize()
            return f"{month_name} {self.year}"

        if self.has_year:
            return str(self.year)

        return self.source_text or "Unknown"

    def matches(self, other: "NormalizedDate", tolerance_days: int = 7) -> bool:
        """Check if this date matches another within tolerance."""
        # Year-only comparison
        if not other.has_month and other.has_year:
            return self.year == other.year

        # Month+year comparison
        if not other.day and other.has_month and other.has_year:
            return self.year == other.year and self.month == other.month

        # Full date comparison with tolerance
        if self.is_complete and other.is_complete:
            try:
                dt1 = datetime(self.year, self.month, self.day)
                dt2 = datetime(other.year, other.month, other.day)
                return abs((dt1 - dt2).days) <= tolerance_days
            except ValueError:
                return False

        return False


@dataclass
class TemporalContext:
    """Context for temporal normalization from episode data."""

    reference_date: datetime | None = None
    session_dates: list[datetime] = field(default_factory=list)
    extracted_dates: list[NormalizedDate] = field(default_factory=list)
    date_mentions: dict[str, list[str]] = field(default_factory=dict)  # date -> episode texts

    def add_session_date(self, date_str: str, episode_text: str = "") -> None:
        """Add a session date from episode metadata."""
        parsed = TemporalNormalizer.parse_date_string(date_str)
        if parsed:
            if parsed not in self.session_dates:
                self.session_dates.append(parsed)
            date_key = parsed.strftime("%Y-%m-%d")
            if date_key not in self.date_mentions:
                self.date_mentions[date_key] = []
            if episode_text:
                self.date_mentions[date_key].append(episode_text)

    def get_earliest_date(self) -> datetime | None:
        """Get the earliest session date."""
        if not self.session_dates:
            return None
        return min(self.session_dates)

    def get_latest_date(self) -> datetime | None:
        """Get the latest session date."""
        if not self.session_dates:
            return None
        return max(self.session_dates)


class TemporalNormalizer:
    """Normalize temporal references in queries and context.

    Converts relative dates ("yesterday", "last week") to absolute dates
    based on the context from stored episodes.
    """

    # Patterns for extracting dates
    DATE_PATTERNS = [
        # "15 July 2023", "July 15, 2023"
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
        # "July 2023"
        r"([A-Za-z]+)\s+(\d{4})",
        # "2023"
        r"\b(20\d{2})\b",
        # "15/07/2023", "2023-07-15"
        r"(\d{1,2})/(\d{1,2})/(\d{4})",
        r"(\d{4})-(\d{2})-(\d{2})",
    ]

    RELATIVE_PATTERNS = {
        r"\byesterday\b": ("days", -1),
        r"\btoday\b": ("days", 0),
        r"\btomorrow\b": ("days", 1),
        r"\blast\s+week\b": ("weeks", -1),
        r"\bthis\s+week\b": ("weeks", 0),
        r"\bnext\s+week\b": ("weeks", 1),
        r"\blast\s+month\b": ("months", -1),
        r"\bthis\s+month\b": ("months", 0),
        r"\bnext\s+month\b": ("months", 1),
        r"\blast\s+year\b": ("years", -1),
        r"\bthis\s+year\b": ("years", 0),
        r"\bnext\s+year\b": ("years", 1),
        r"\ba\s+few\s+days\s+ago\b": ("days", -3),
        r"\ba\s+week\s+ago\b": ("weeks", -1),
        r"\ba\s+month\s+ago\b": ("months", -1),
        r"\brecently\b": ("days", -7),
    }

    @staticmethod
    def parse_date_string(date_str: str) -> datetime | None:
        """Parse a date string into a datetime object."""
        # Try common formats
        formats = [
            "%d %B %Y",      # 15 July 2023
            "%B %d, %Y",     # July 15, 2023
            "%B %d %Y",      # July 15 2023
            "%Y-%m-%d",      # 2023-07-15
            "%d/%m/%Y",      # 15/07/2023
            "%m/%d/%Y",      # 07/15/2023
            "%B %Y",         # July 2023
            "%Y",            # 2023
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        # Try regex extraction
        for pattern in TemporalNormalizer.DATE_PATTERNS:
            match = re.search(pattern, date_str, re.IGNORECASE)
            if match:
                groups = match.groups()
                try:
                    if len(groups) == 3:
                        # Full date
                        if groups[0].isdigit() and len(groups[0]) == 4:
                            # YYYY-MM-DD format
                            return datetime(int(groups[0]), int(groups[1]), int(groups[2]), tzinfo=timezone.utc)
                        elif groups[2].isdigit() and len(groups[2]) == 4:
                            # DD Month YYYY or Month DD YYYY
                            day = int(groups[0]) if groups[0].isdigit() else int(groups[1])
                            month_str = groups[1] if not groups[1].isdigit() else groups[0]
                            month = MONTH_NAMES.get(month_str.lower(), 1)
                            year = int(groups[2])
                            return datetime(year, month, day, tzinfo=timezone.utc)
                    elif len(groups) == 2:
                        # Month Year
                        month = MONTH_NAMES.get(groups[0].lower(), 1)
                        year = int(groups[1])
                        return datetime(year, month, 1, tzinfo=timezone.utc)
                    elif len(groups) == 1:
                        # Year only
                        return datetime(int(groups[0]), 1, 1, tzinfo=timezone.utc)
                except (ValueError, KeyError):
                    continue

        return None

    def __init__(self, reference_date: datetime | None = None):
        """Initialize with optional reference date.

        Args:
            reference_date: Base date for relative date calculations.
                           Defaults to current time.
        """
        self._reference_date = reference_date or datetime.now(timezone.utc)

    def set_reference_date(self, date: datetime) -> None:
        """Set the reference date for relative calculations."""
        self._reference_date = date

    def extract_dates_from_text(self, text: str) -> list[NormalizedDate]:
        """Extract all dates mentioned in text."""
        dates = []

        # Check for absolute dates first
        for pattern in self.DATE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                dt = self.parse_date_string(match.group())
                if dt:
                    dates.append(NormalizedDate(
                        year=dt.year,
                        month=dt.month,
                        day=dt.day,
                        confidence=0.9,
                        source_text=match.group(),
                        is_relative=False,
                    ))

        return dates

    def resolve_relative_date(
        self,
        relative_expr: str,
        reference: datetime | None = None,
    ) -> NormalizedDate | None:
        """Resolve a relative date expression to an absolute date.

        Args:
            relative_expr: Expression like "yesterday", "last week".
            reference: Reference date. Uses instance reference if None.

        Returns:
            NormalizedDate or None if not parseable.
        """
        ref = reference or self._reference_date
        text_lower = relative_expr.lower()

        for pattern, (unit, offset) in self.RELATIVE_PATTERNS.items():
            if re.search(pattern, text_lower):
                try:
                    if unit == "days":
                        dt = ref + timedelta(days=offset)
                    elif unit == "weeks":
                        dt = ref + timedelta(weeks=offset)
                    elif unit == "months":
                        # Approximate month calculation
                        dt = ref + timedelta(days=offset * 30)
                    elif unit == "years":
                        dt = ref.replace(year=ref.year + offset)
                    else:
                        continue

                    return NormalizedDate(
                        year=dt.year,
                        month=dt.month,
                        day=dt.day,
                        confidence=0.8,
                        source_text=relative_expr,
                        is_relative=True,
                    )
                except (ValueError, OverflowError):
                    continue

        return None

    def normalize_query(
        self,
        query: str,
        context: TemporalContext | None = None,
    ) -> tuple[str, list[NormalizedDate]]:
        """Normalize temporal references in a query.

        Replaces relative dates with absolute dates for better matching.

        Args:
            query: The query to normalize.
            context: Temporal context from episodes.

        Returns:
            Tuple of (normalized query, extracted dates).
        """
        normalized = query
        dates = []

        # Use context reference date if available
        ref_date = self._reference_date
        if context and context.reference_date:
            ref_date = context.reference_date
        elif context and context.session_dates:
            ref_date = context.get_latest_date() or ref_date

        # Extract absolute dates
        abs_dates = self.extract_dates_from_text(query)
        dates.extend(abs_dates)

        # Resolve relative dates
        for pattern, (unit, offset) in self.RELATIVE_PATTERNS.items():
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                rel_date = self.resolve_relative_date(match.group(), ref_date)
                if rel_date:
                    dates.append(rel_date)
                    # Replace relative with absolute in normalized query
                    normalized = re.sub(
                        pattern,
                        f"({rel_date.to_string()})",
                        normalized,
                        flags=re.IGNORECASE,
                    )

        return normalized, dates

    def build_temporal_context(
        self,
        episodes: list[Any],
    ) -> TemporalContext:
        """Build temporal context from a list of episodes.

        Args:
            episodes: Episodes with created_at timestamps.

        Returns:
            TemporalContext with extracted dates.
        """
        context = TemporalContext()

        for episode in episodes:
            # Get timestamp from episode
            created_at = getattr(episode, 'created_at', None)
            if created_at:
                if isinstance(created_at, str):
                    parsed = self.parse_date_string(created_at)
                    if parsed:
                        context.session_dates.append(parsed)
                elif isinstance(created_at, datetime):
                    context.session_dates.append(created_at)

            # Extract dates from episode content
            content = getattr(episode, 'content', '') or ''
            if isinstance(content, str):
                dates = self.extract_dates_from_text(content)
                context.extracted_dates.extend(dates)

        # Set reference to latest session
        if context.session_dates:
            context.reference_date = context.get_latest_date()

        return context

    def format_temporal_context(
        self,
        context: TemporalContext,
    ) -> str:
        """Format temporal context as a header for LLM context.

        This helps the LLM understand the time frame of the conversation.
        """
        if not context.session_dates:
            return ""

        lines = ["=== TEMPORAL CONTEXT ==="]

        earliest = context.get_earliest_date()
        latest = context.get_latest_date()

        if earliest and latest:
            if earliest == latest:
                lines.append(f"Conversation date: {earliest.strftime('%B %d, %Y')}")
            else:
                lines.append(f"Conversation period: {earliest.strftime('%B %d, %Y')} to {latest.strftime('%B %d, %Y')}")

        if context.extracted_dates:
            mentioned_dates = sorted(set(d.to_string() for d in context.extracted_dates[:5]))
            if mentioned_dates:
                lines.append(f"Dates mentioned: {', '.join(mentioned_dates)}")

        lines.append("=" * 25)
        return "\n".join(lines)

    def answer_temporal_question(
        self,
        query: str,
        context: TemporalContext,
    ) -> str | None:
        """Attempt to directly answer a temporal question from context.

        For questions like "When did X happen?", tries to find the answer
        from the temporal context without needing LLM inference.

        Args:
            query: The temporal question.
            context: Temporal context from episodes.

        Returns:
            Direct answer string if found, None otherwise.
        """
        query_lower = query.lower()

        # Extract the subject of the temporal question
        # E.g., "When did Caroline paint?" -> "caroline paint"
        temporal_patterns = [
            r"when\s+did\s+(\w+)\s+(.+?)\?",
            r"when\s+was\s+(\w+)\s+(.+?)\?",
            r"when\s+is\s+(\w+)\s+(.+?)\?",
        ]

        for pattern in temporal_patterns:
            match = re.search(pattern, query_lower)
            if match:
                subject = match.group(1)
                action = match.group(2)

                # Search date mentions for this subject/action
                for date_str, texts in context.date_mentions.items():
                    for text in texts:
                        text_lower = text.lower()
                        if subject in text_lower and any(a in text_lower for a in action.split()):
                            # Found a match - return the date
                            parsed = self.parse_date_string(date_str)
                            if parsed:
                                return parsed.strftime("%B %Y")

        return None

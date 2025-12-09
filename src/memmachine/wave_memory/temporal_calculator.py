"""Temporal Calculator - Date arithmetic for wave memory.

Handles complex temporal expressions like:
- "The sunday before 25 May 2023" -> calculates exact date
- "4 years ago" -> calculates from reference date
- "The week before June 9" -> calculates date range

This is critical for answering temporal questions accurately.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from dataclasses import dataclass
from calendar import monthrange


# Month name to number mapping
MONTHS = {
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

# Weekday name to number (Monday=0)
WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


@dataclass
class TemporalResult:
    """Result of temporal calculation."""

    calculated_date: datetime | None = None
    date_range: tuple[datetime, datetime] | None = None
    duration: timedelta | None = None
    duration_text: str = ""
    confidence: float = 0.0
    calculation_type: str = ""  # "relative_weekday", "duration", "absolute", etc.
    raw_expression: str = ""


class TemporalCalculator:
    """Calculate dates from complex temporal expressions.

    Handles:
    - "The sunday before 25 May 2023"
    - "The week before June 9, 2023"
    - "4 years ago"
    - "Last Monday"
    - Duration calculations
    """

    def __init__(self, default_year: int | None = None):
        """Initialize calculator.

        Args:
            default_year: Year to assume when not specified. Defaults to current year.
        """
        self.default_year = default_year or datetime.now().year

    def calculate(
        self,
        expression: str,
        reference_date: datetime | None = None,
    ) -> TemporalResult:
        """Calculate date from temporal expression.

        Args:
            expression: Temporal expression to parse.
            reference_date: Reference date for relative calculations.

        Returns:
            TemporalResult with calculated date/duration.
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        expr_lower = expression.lower().strip()

        # Try each pattern in order of specificity

        # Pattern: "The [weekday] before [date]"
        result = self._parse_weekday_before_date(expr_lower, reference_date)
        if result.calculated_date:
            return result

        # Pattern: "The week before [date]"
        result = self._parse_week_before_date(expr_lower, reference_date)
        if result.date_range:
            return result

        # Pattern: "N [units] ago"
        result = self._parse_duration_ago(expr_lower, reference_date)
        if result.calculated_date:
            return result

        # Pattern: "last [weekday]"
        result = self._parse_last_weekday(expr_lower, reference_date)
        if result.calculated_date:
            return result

        # Pattern: Absolute date "[Month] [day], [year]" or "[Month] [day]"
        result = self._parse_absolute_date(expr_lower, reference_date)
        if result.calculated_date:
            return result

        # Pattern: Just month and year "June 2023"
        result = self._parse_month_year(expr_lower)
        if result.calculated_date:
            return result

        return TemporalResult(raw_expression=expression)

    def calculate_duration_between(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> TemporalResult:
        """Calculate human-readable duration between two dates.

        Args:
            start_date: Start date.
            end_date: End date.

        Returns:
            TemporalResult with duration info.
        """
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        delta = end_date - start_date

        # Calculate in various units
        days = delta.days
        years = days // 365
        months = (days % 365) // 30
        weeks = days // 7

        # Build human-readable duration
        if years >= 1:
            if years == 1:
                duration_text = "1 year"
            else:
                duration_text = f"{years} years"
            if months > 0:
                duration_text += f" and {months} months"
        elif months >= 1:
            if months == 1:
                duration_text = "1 month"
            else:
                duration_text = f"{months} months"
        elif weeks >= 1:
            if weeks == 1:
                duration_text = "1 week"
            else:
                duration_text = f"{weeks} weeks"
        else:
            if days == 1:
                duration_text = "1 day"
            else:
                duration_text = f"{days} days"

        return TemporalResult(
            duration=delta,
            duration_text=duration_text,
            confidence=0.95,
            calculation_type="duration",
        )

    def _parse_weekday_before_date(
        self,
        expr: str,
        reference_date: datetime,
    ) -> TemporalResult:
        """Parse 'the [weekday] before [date]' expressions."""

        # Pattern: "the sunday before 25 may 2023"
        pattern = r"the\s+(\w+day)\s+before\s+(\d{1,2})\s+(\w+)\s+(\d{4})"
        match = re.search(pattern, expr)

        if not match:
            # Try: "the sunday before may 25, 2023"
            pattern = r"the\s+(\w+day)\s+before\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})"
            match = re.search(pattern, expr)
            if match:
                weekday_name = match.group(1)
                month_name = match.group(2)
                day = int(match.group(3))
                year = int(match.group(4))
            else:
                return TemporalResult()
        else:
            weekday_name = match.group(1)
            day = int(match.group(2))
            month_name = match.group(3)
            year = int(match.group(4))

        # Get target weekday number
        target_weekday = WEEKDAYS.get(weekday_name.lower())
        if target_weekday is None:
            return TemporalResult()

        # Get month number
        month = MONTHS.get(month_name.lower())
        if month is None:
            return TemporalResult()

        # Create the reference date
        try:
            ref_date = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return TemporalResult()

        # Find the weekday before this date
        calculated = self._find_weekday_before(ref_date, target_weekday)

        return TemporalResult(
            calculated_date=calculated,
            confidence=0.95,
            calculation_type="weekday_before_date",
            raw_expression=expr,
        )

    def _parse_week_before_date(
        self,
        expr: str,
        reference_date: datetime,
    ) -> TemporalResult:
        """Parse 'the week before [date]' expressions."""

        # Pattern: "the week before 9 june 2023"
        pattern = r"the\s+week\s+before\s+(\d{1,2})\s+(\w+)\s+(\d{4})"
        match = re.search(pattern, expr)

        if not match:
            # Try: "the week before june 9, 2023"
            pattern = r"the\s+week\s+before\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})"
            match = re.search(pattern, expr)
            if match:
                month_name = match.group(1)
                day = int(match.group(2))
                year = int(match.group(3))
            else:
                return TemporalResult()
        else:
            day = int(match.group(1))
            month_name = match.group(2)
            year = int(match.group(3))

        month = MONTHS.get(month_name.lower())
        if month is None:
            return TemporalResult()

        try:
            ref_date = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return TemporalResult()

        # The week before = 7 days before to 1 day before
        week_end = ref_date - timedelta(days=1)
        week_start = ref_date - timedelta(days=7)

        # Return the middle of the week as the "date"
        mid_week = ref_date - timedelta(days=4)

        return TemporalResult(
            calculated_date=mid_week,
            date_range=(week_start, week_end),
            confidence=0.85,
            calculation_type="week_before_date",
            raw_expression=expr,
        )

    def _parse_duration_ago(
        self,
        expr: str,
        reference_date: datetime,
    ) -> TemporalResult:
        """Parse 'N [units] ago' expressions."""

        pattern = r"(\d+)\s+(year|month|week|day)s?\s+ago"
        match = re.search(pattern, expr)

        if not match:
            # Handle "a few years ago" -> ~3 years
            if "few years ago" in expr:
                years_back = 3
                calculated = reference_date - timedelta(days=years_back * 365)
                return TemporalResult(
                    calculated_date=calculated,
                    duration_text=f"about {years_back} years ago",
                    confidence=0.6,
                    calculation_type="duration_ago",
                    raw_expression=expr,
                )
            return TemporalResult()

        amount = int(match.group(1))
        unit = match.group(2)

        if unit == "year":
            delta = timedelta(days=amount * 365)
        elif unit == "month":
            delta = timedelta(days=amount * 30)
        elif unit == "week":
            delta = timedelta(weeks=amount)
        else:  # day
            delta = timedelta(days=amount)

        calculated = reference_date - delta

        return TemporalResult(
            calculated_date=calculated,
            duration=delta,
            duration_text=f"{amount} {unit}s ago",
            confidence=0.9,
            calculation_type="duration_ago",
            raw_expression=expr,
        )

    def _parse_last_weekday(
        self,
        expr: str,
        reference_date: datetime,
    ) -> TemporalResult:
        """Parse 'last [weekday]' expressions."""

        pattern = r"last\s+(\w+day)"
        match = re.search(pattern, expr)

        if not match:
            return TemporalResult()

        weekday_name = match.group(1)
        target_weekday = WEEKDAYS.get(weekday_name.lower())

        if target_weekday is None:
            return TemporalResult()

        calculated = self._find_weekday_before(reference_date, target_weekday)

        return TemporalResult(
            calculated_date=calculated,
            confidence=0.85,
            calculation_type="last_weekday",
            raw_expression=expr,
        )

    def _parse_absolute_date(
        self,
        expr: str,
        reference_date: datetime,
    ) -> TemporalResult:
        """Parse absolute date expressions."""

        # Pattern: "25 may 2023" or "may 25, 2023"
        patterns = [
            (r"(\d{1,2})\s+(\w+)\s+(\d{4})", lambda m: (int(m.group(1)), m.group(2), int(m.group(3)))),
            (r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", lambda m: (int(m.group(2)), m.group(1), int(m.group(3)))),
            # Without year
            (r"(\d{1,2})\s+(\w+)(?!\s*\d{4})", lambda m: (int(m.group(1)), m.group(2), self.default_year)),
            (r"(\w+)\s+(\d{1,2})(?!,?\s*\d{4})", lambda m: (int(m.group(2)), m.group(1), self.default_year)),
        ]

        for pattern, extractor in patterns:
            match = re.search(pattern, expr)
            if match:
                try:
                    day, month_name, year = extractor(match)
                    month = MONTHS.get(month_name.lower())
                    if month:
                        calculated = datetime(year, month, day, tzinfo=timezone.utc)
                        return TemporalResult(
                            calculated_date=calculated,
                            confidence=0.95,
                            calculation_type="absolute_date",
                            raw_expression=expr,
                        )
                except (ValueError, TypeError):
                    continue

        return TemporalResult()

    def _parse_month_year(self, expr: str) -> TemporalResult:
        """Parse 'Month Year' expressions like 'June 2023'."""

        pattern = r"(\w+)\s+(\d{4})"
        match = re.search(pattern, expr)

        if not match:
            return TemporalResult()

        month_name = match.group(1)
        year = int(match.group(2))

        month = MONTHS.get(month_name.lower())
        if month is None:
            return TemporalResult()

        # Return first of month
        try:
            calculated = datetime(year, month, 1, tzinfo=timezone.utc)
            return TemporalResult(
                calculated_date=calculated,
                confidence=0.8,
                calculation_type="month_year",
                raw_expression=expr,
            )
        except ValueError:
            return TemporalResult()

    def _find_weekday_before(
        self,
        reference_date: datetime,
        target_weekday: int,
    ) -> datetime:
        """Find the most recent occurrence of a weekday before reference_date."""

        # Start from reference_date and go back
        current = reference_date - timedelta(days=1)  # Start from day before

        while current.weekday() != target_weekday:
            current -= timedelta(days=1)

        return current

    def format_date(self, dt: datetime | None) -> str:
        """Format date in human-readable form."""
        if dt is None:
            return ""
        return dt.strftime("%B %d, %Y")


# =============================================================================
# TEMPORAL CONTEXT ENHANCER
# =============================================================================

class TemporalContextEnhancer:
    """Enhances context with calculated temporal information.

    Used during question answering to provide explicit dates
    when questions ask about relative time expressions.
    """

    def __init__(self):
        self.calculator = TemporalCalculator()

    def enhance_for_question(
        self,
        question: str,
        memories: list[dict[str, Any]],
        reference_date: datetime | None = None,
    ) -> str:
        """Generate temporal hints for a question.

        Args:
            question: The question being asked.
            memories: Retrieved memories with timestamps.
            reference_date: Reference date for calculations.

        Returns:
            String with temporal calculations to append to context.
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        hints = []
        q_lower = question.lower()

        # Check if this is a temporal question
        is_temporal = any(trigger in q_lower for trigger in [
            "when ", "what date", "what day", "how long",
            "since when", "how many years", "how many months",
        ])

        if not is_temporal:
            return ""

        # Look for relative expressions in memories that might answer the question
        for mem in memories:
            content = mem.get("content", mem.get("text", ""))
            timestamp = mem.get("timestamp")

            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    timestamp = None

            if not timestamp:
                continue

            # Check for relative temporal expressions in memory content
            relative_patterns = [
                r"the\s+\w+day\s+before",
                r"the\s+week\s+before",
                r"last\s+\w+day",
                r"\d+\s+(year|month|week|day)s?\s+ago",
            ]

            for pattern in relative_patterns:
                match = re.search(pattern, content.lower())
                if match:
                    result = self.calculator.calculate(match.group(0), timestamp)
                    if result.calculated_date:
                        date_str = self.calculator.format_date(result.calculated_date)
                        hints.append(f"'{match.group(0)}' = {date_str}")

        # Also check for "how long" questions - calculate durations
        if "how long" in q_lower:
            # Look for date spans in memories
            dates_found = []
            for mem in memories:
                timestamp = mem.get("timestamp")
                if isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        dates_found.append(timestamp)
                    except ValueError:
                        pass
                elif isinstance(timestamp, datetime):
                    dates_found.append(timestamp)

            if len(dates_found) >= 2:
                dates_found.sort()
                duration = self.calculator.calculate_duration_between(
                    dates_found[0], dates_found[-1]
                )
                if duration.duration_text:
                    hints.append(f"Time span in memories: {duration.duration_text}")

        if hints:
            return "\n\nTEMPORAL CALCULATIONS:\n" + "\n".join(f"- {h}" for h in hints)

        return ""

    def extract_session_date(
        self,
        session_time: str,
    ) -> datetime | None:
        """Extract date from session time string."""
        if not session_time or session_time == "Unknown":
            return None

        formats = [
            "%B %d, %Y %I:%M %p",
            "%B %d, %Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(session_time, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        return None


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def calculate_date(
    expression: str,
    reference_date: datetime | None = None,
) -> datetime | None:
    """Quick helper to calculate a date from expression.

    Args:
        expression: Temporal expression like "the sunday before may 25, 2023"
        reference_date: Reference for relative calculations.

    Returns:
        Calculated datetime or None.
    """
    calc = TemporalCalculator()
    result = calc.calculate(expression, reference_date)
    return result.calculated_date


def calculate_duration(
    start: datetime,
    end: datetime,
) -> str:
    """Quick helper to get human-readable duration.

    Args:
        start: Start date.
        end: End date.

    Returns:
        Human-readable duration string like "4 years".
    """
    calc = TemporalCalculator()
    result = calc.calculate_duration_between(start, end)
    return result.duration_text

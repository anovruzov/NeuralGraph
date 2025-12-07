"""Temporal normalizer - resolves relative dates to absolute dates."""

import re
from datetime import datetime, timedelta


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class TemporalNormalizer:
    """Resolves relative temporal expressions to absolute dates.

    This prevents LLMs from having to do date arithmetic at inference time,
    which small models fail at consistently.
    """

    def normalize(self, text: str, reference_date: datetime) -> str:
        """Replace relative temporal expressions with absolute dates.

        Args:
            text: The text containing relative temporal expressions.
            reference_date: The reference date (e.g., conversation timestamp).

        Returns:
            Text with relative dates replaced by absolute dates.
        """
        # Order matters - more specific patterns first
        text = self._replace_last_weekday(text, reference_date)
        text = self._replace_next_weekday(text, reference_date)
        text = self._replace_this_weekday(text, reference_date)
        text = self._replace_yesterday(text, reference_date)
        text = self._replace_today(text, reference_date)
        text = self._replace_tomorrow(text, reference_date)
        text = self._replace_day_before_yesterday(text, reference_date)
        text = self._replace_day_after_tomorrow(text, reference_date)
        text = self._replace_last_week(text, reference_date)
        text = self._replace_this_week(text, reference_date)
        text = self._replace_next_week(text, reference_date)
        text = self._replace_last_month(text, reference_date)
        text = self._replace_this_month(text, reference_date)
        text = self._replace_next_month(text, reference_date)
        text = self._replace_last_year(text, reference_date)
        text = self._replace_this_year(text, reference_date)
        text = self._replace_next_year(text, reference_date)
        text = self._replace_few_days_ago(text, reference_date)
        text = self._replace_other_day(text, reference_date)
        text = self._replace_recently(text, reference_date)

        return text

    def _format_date(self, dt: datetime) -> str:
        """Format date as 'Month Day, Year'."""
        return dt.strftime("%B %d, %Y")

    def _replace_yesterday(self, text: str, ref: datetime) -> str:
        yesterday = ref - timedelta(days=1)
        date_str = self._format_date(yesterday)
        return re.sub(r"\byesterday\b", f"on {date_str}", text, flags=re.IGNORECASE)

    def _replace_today(self, text: str, ref: datetime) -> str:
        date_str = self._format_date(ref)
        return re.sub(r"\btoday\b", f"on {date_str}", text, flags=re.IGNORECASE)

    def _replace_tomorrow(self, text: str, ref: datetime) -> str:
        tomorrow = ref + timedelta(days=1)
        date_str = self._format_date(tomorrow)
        return re.sub(r"\btomorrow\b", f"on {date_str}", text, flags=re.IGNORECASE)

    def _replace_day_before_yesterday(self, text: str, ref: datetime) -> str:
        day = ref - timedelta(days=2)
        date_str = self._format_date(day)
        return re.sub(
            r"\b(the day before yesterday|day before yesterday)\b",
            f"on {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_day_after_tomorrow(self, text: str, ref: datetime) -> str:
        day = ref + timedelta(days=2)
        date_str = self._format_date(day)
        return re.sub(
            r"\b(the day after tomorrow|day after tomorrow)\b",
            f"on {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_last_weekday(self, text: str, ref: datetime) -> str:
        """Replace 'last Monday', 'last Tuesday', etc."""
        for i, day in enumerate(WEEKDAYS):
            pattern = rf"\blast {day}\b"
            if re.search(pattern, text, re.IGNORECASE):
                # Find the most recent past occurrence of this weekday
                days_back = (ref.weekday() - i) % 7
                if days_back == 0:
                    days_back = 7  # If today is that day, go back a week
                target = ref - timedelta(days=days_back)
                date_str = self._format_date(target)
                text = re.sub(pattern, f"on {date_str}", text, flags=re.IGNORECASE)
        return text

    def _replace_next_weekday(self, text: str, ref: datetime) -> str:
        """Replace 'next Monday', 'next Tuesday', etc."""
        for i, day in enumerate(WEEKDAYS):
            pattern = rf"\bnext {day}\b"
            if re.search(pattern, text, re.IGNORECASE):
                # Find the next occurrence of this weekday
                days_forward = (i - ref.weekday()) % 7
                if days_forward == 0:
                    days_forward = 7  # If today is that day, go forward a week
                target = ref + timedelta(days=days_forward)
                date_str = self._format_date(target)
                text = re.sub(pattern, f"on {date_str}", text, flags=re.IGNORECASE)
        return text

    def _replace_this_weekday(self, text: str, ref: datetime) -> str:
        """Replace 'this Monday', 'this Tuesday', etc."""
        for i, day in enumerate(WEEKDAYS):
            pattern = rf"\bthis {day}\b"
            if re.search(pattern, text, re.IGNORECASE):
                # Find this week's occurrence
                days_diff = i - ref.weekday()
                target = ref + timedelta(days=days_diff)
                date_str = self._format_date(target)
                text = re.sub(pattern, f"on {date_str}", text, flags=re.IGNORECASE)
        return text

    def _replace_last_week(self, text: str, ref: datetime) -> str:
        last_week_start = ref - timedelta(days=ref.weekday() + 7)
        date_str = self._format_date(last_week_start)
        return re.sub(
            r"\blast week\b",
            f"during the week of {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_this_week(self, text: str, ref: datetime) -> str:
        week_start = ref - timedelta(days=ref.weekday())
        date_str = self._format_date(week_start)
        return re.sub(
            r"\bthis week\b",
            f"during the week of {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_next_week(self, text: str, ref: datetime) -> str:
        next_week_start = ref + timedelta(days=7 - ref.weekday())
        date_str = self._format_date(next_week_start)
        return re.sub(
            r"\bnext week\b",
            f"during the week of {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_last_month(self, text: str, ref: datetime) -> str:
        if ref.month == 1:
            last_month = datetime(ref.year - 1, 12, 1)
        else:
            last_month = datetime(ref.year, ref.month - 1, 1)
        month_str = last_month.strftime("%B %Y")
        return re.sub(
            r"\blast month\b", f"in {month_str}", text, flags=re.IGNORECASE
        )

    def _replace_this_month(self, text: str, ref: datetime) -> str:
        month_str = ref.strftime("%B %Y")
        return re.sub(
            r"\bthis month\b", f"in {month_str}", text, flags=re.IGNORECASE
        )

    def _replace_next_month(self, text: str, ref: datetime) -> str:
        if ref.month == 12:
            next_month = datetime(ref.year + 1, 1, 1)
        else:
            next_month = datetime(ref.year, ref.month + 1, 1)
        month_str = next_month.strftime("%B %Y")
        return re.sub(
            r"\bnext month\b", f"in {month_str}", text, flags=re.IGNORECASE
        )

    def _replace_last_year(self, text: str, ref: datetime) -> str:
        return re.sub(
            r"\blast year\b", f"in {ref.year - 1}", text, flags=re.IGNORECASE
        )

    def _replace_this_year(self, text: str, ref: datetime) -> str:
        return re.sub(
            r"\bthis year\b", f"in {ref.year}", text, flags=re.IGNORECASE
        )

    def _replace_next_year(self, text: str, ref: datetime) -> str:
        return re.sub(
            r"\bnext year\b", f"in {ref.year + 1}", text, flags=re.IGNORECASE
        )

    def _replace_few_days_ago(self, text: str, ref: datetime) -> str:
        approx_date = ref - timedelta(days=3)
        date_str = self._format_date(approx_date)
        return re.sub(
            r"\b(a few days ago|few days ago)\b",
            f"around {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_other_day(self, text: str, ref: datetime) -> str:
        approx_date = ref - timedelta(days=2)
        date_str = self._format_date(approx_date)
        return re.sub(
            r"\bthe other day\b",
            f"around {date_str}",
            text,
            flags=re.IGNORECASE,
        )

    def _replace_recently(self, text: str, ref: datetime) -> str:
        approx_date = ref - timedelta(days=7)
        date_str = self._format_date(approx_date)
        return re.sub(
            r"\brecently\b",
            f"around {date_str}",
            text,
            flags=re.IGNORECASE,
        )


# Singleton instance for convenience
_normalizer = TemporalNormalizer()


def normalize_temporal(text: str, reference_date: datetime) -> str:
    """Convenience function for temporal normalization."""
    return _normalizer.normalize(text, reference_date)

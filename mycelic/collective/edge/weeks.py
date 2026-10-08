"""ISO weeks, the one time unit the site store, the Boundary and :class:`~.site.EdgeSite` share.

Rules:

* A week is ``YYYY-Www`` from ``date.isocalendar()``, so a year has 52 or 53 weeks and 1 January can belong to the
  previous year (2027-01-01 is in 2026-W53). Years have four digits and weeks two, so week strings compare
  correctly as strings; ``max()`` and range checks rely on that.
* A week is closed at ``as_of`` with lag ``L`` when its Sunday plus ``L`` days is on or before ``as_of``;
  :func:`closed_through` is the last closed week.
* :func:`local_date` reads the date a record was received in the sender's own calendar: a plain ``YYYY-MM-DD``, or
  the first ten characters of an ISO 8601 timestamp whose fields are in range. It never converts a time zone, so
  ``2026-03-01T23:30:00-05:00`` is 2026-03-01. Anything else (another type, an impossible date, hour 25, a space
  instead of ``T``, non-ASCII digits) is None.

Pure functions of their arguments; no clock is read here.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from ..packs.connector import ISO_DATE_RE, valid_date

WEEK_RE = re.compile(r"[0-9]{4}-W[0-9]{2}", re.ASCII)
TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,9})?)?(Z|[+-][0-9]{2}:[0-9]{2})?",
                   re.ASCII)
MAX_OFFSET_HOURS = 14


def _day(value: date | str) -> date:
    if isinstance(value, date):
        return value
    if not valid_date(value):
        raise ValueError("not a calendar date YYYY-MM-DD") from None
    return date.fromisoformat(value)


def iso_week(day: date | str) -> str:
    year, week, _ = _day(day).isocalendar()
    return f"{year:04d}-W{week:02d}"


def valid_week(text: Any) -> bool:
    if not isinstance(text, str) or WEEK_RE.fullmatch(text) is None:
        return False
    try:
        date.fromisocalendar(int(text[:4]), int(text[6:]), 1)
    except ValueError:
        return False
    return True


def week_monday(week: str) -> date:
    if not valid_week(week):
        raise ValueError("not an ISO week YYYY-Www") from None
    return date.fromisocalendar(int(week[:4]), int(week[6:]), 1)


def week_sunday(week: str) -> date:
    return week_monday(week) + timedelta(days=6)


def next_week(week: str) -> str:
    return iso_week(week_monday(week) + timedelta(days=7))


def closed_through(as_of: date | str, close_lag_days: int) -> str:
    """The last week W with ``week_sunday(W) + close_lag_days <= as_of``."""
    if isinstance(close_lag_days, bool) or not isinstance(close_lag_days, int) or close_lag_days < 0:
        raise ValueError("close_lag_days must be an int >= 0") from None
    d = _day(as_of) - timedelta(days=close_lag_days)
    return iso_week(d - timedelta(days=d.isoweekday() % 7))


def local_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if ISO_DATE_RE.fullmatch(value) is not None:
        return value if valid_date(value) else None
    m = TS_RE.fullmatch(value)
    if m is None or not valid_date(value[:10]):
        return None
    if int(value[11:13]) >= 24 or int(value[14:16]) >= 60:
        return None
    if m.group(1) is not None and int(m.group(1)[1:3]) >= 60:
        return None
    offset = m.group(3)
    if offset is not None and offset != "Z" and (int(offset[1:3]) > MAX_OFFSET_HOURS or int(offset[4:6]) >= 60):
        return None
    return value[:10]

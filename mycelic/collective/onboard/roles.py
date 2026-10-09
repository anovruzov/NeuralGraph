"""Column roles (D001 rule 1.2) and dates (rule 1.3).

**The roles file** is ``{"language": "en", "roles": {"record_id", "site", "date", "narrative", "category",
"entities": [...], "reporter": null|column, "forbidden": [...]}}``: one column per single role, each column at most
one role. :func:`check_roles` checks every declared column against the export's header.

**Dates.** The part of a value before its first space or ``T`` is parsed, in the formats of :data:`DATE_FORMATS`
(``MON`` is a month abbreviation of the language file, any case; a two-digit year ``YY`` is ``20YY`` below 70, else
``19YY``). :func:`choose_date_format` picks the first format that parses at least the given share of the non-empty
values.

**Inference without hints** (reported only; the scored run uses the declared roles). The evidence per column
(:func:`column_evidence`) is counted over the given rows: fill (the share of rows with a value), distinct values,
mean ``\\w+`` tokens per value, the date share (the largest share of values one format parses), the letter share and
the header words (the header split at every character that is not a letter or digit, lower-cased). In a list column
every element is a value. :func:`infer_roles` applies the five rules in order, each to the columns no earlier rule
took; :func:`roles_right` counts how many of the five declared roles the inference found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..jsonio import StrictJsonError, strict_load
from .exports import Export

SINGLE_ROLES = ("record_id", "site", "date", "narrative", "category")
ROLE_KEYS = (*SINGLE_ROLES, "entities", "reporter", "forbidden")
DATE_FORMATS = ("YYYY-MM-DD", "YYYYMMDD", "YYYY/MM/DD", "M/D/YYYY", "M/D/YY", "D.M.YYYY", "D-MON-YYYY", "D-MON-YY")
_DATE_RE = {
    "YYYY-MM-DD": re.compile(r"(?P<y>[0-9]{4})-(?P<m>[0-9]{2})-(?P<d>[0-9]{2})", re.ASCII),
    "YYYYMMDD": re.compile(r"(?P<y>[0-9]{4})(?P<m>[0-9]{2})(?P<d>[0-9]{2})", re.ASCII),
    "YYYY/MM/DD": re.compile(r"(?P<y>[0-9]{4})/(?P<m>[0-9]{2})/(?P<d>[0-9]{2})", re.ASCII),
    "M/D/YYYY": re.compile(r"(?P<m>[0-9]{1,2})/(?P<d>[0-9]{1,2})/(?P<y>[0-9]{4})", re.ASCII),
    "M/D/YY": re.compile(r"(?P<m>[0-9]{1,2})/(?P<d>[0-9]{1,2})/(?P<yy>[0-9]{2})", re.ASCII),
    "D.M.YYYY": re.compile(r"(?P<d>[0-9]{1,2})\.(?P<m>[0-9]{1,2})\.(?P<y>[0-9]{4})", re.ASCII),
    "D-MON-YYYY": re.compile(r"(?P<d>[0-9]{1,2})-(?P<mon>[A-Za-z]{3})-(?P<y>[0-9]{4})", re.ASCII),
    "D-MON-YY": re.compile(r"(?P<d>[0-9]{1,2})-(?P<mon>[A-Za-z]{3})-(?P<yy>[0-9]{2})", re.ASCII),
}
_DATE_CUT = re.compile(r"[ T]")
_TOKEN = re.compile(r"\w+")
_LETTER = re.compile(r"[^\W\d_]")
_HEADER_SPLIT = re.compile(r"[^0-9A-Za-z]+")
TWO_DIGIT_PIVOT = 70


class RolesError(ValueError):
    """A roles file that is malformed, or that names a column the export does not have."""


@dataclass(frozen=True)
class Roles:
    language: str
    record_id: str
    site: str
    date: str
    narrative: str
    category: str
    entities: tuple[str, ...]
    reporter: str | None
    forbidden: tuple[str, ...]

    def single(self) -> dict[str, str]:
        return {r: getattr(self, r) for r in SINGLE_ROLES}

    def columns(self) -> list[str]:
        out = [getattr(self, r) for r in SINGLE_ROLES]
        out += list(self.entities)
        if self.reporter is not None:
            out.append(self.reporter)
        out += list(self.forbidden)
        return out

    def to_json(self) -> dict[str, Any]:
        return {"language": self.language,
                "roles": {**self.single(), "entities": list(self.entities), "reporter": self.reporter,
                          "forbidden": list(self.forbidden)}}


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RolesError(f"{what} must be a column name") from None
    return value


def roles_from_json(obj: Any) -> Roles:
    if not isinstance(obj, dict) or sorted(obj) != ["language", "roles"]:
        raise RolesError("a roles file is {\"language\": ..., \"roles\": {...}}") from None
    language = obj["language"]
    if not isinstance(language, str) or re.fullmatch(r"[a-z]{2,3}", language) is None:
        raise RolesError("language must be a language code such as en") from None
    raw = obj["roles"]
    if not isinstance(raw, dict) or sorted(raw) != sorted(ROLE_KEYS):
        raise RolesError(f"roles must have exactly the keys {', '.join(ROLE_KEYS)}") from None
    single = {r: _name(raw[r], r) for r in SINGLE_ROLES}
    for key in ("entities", "forbidden"):
        if not isinstance(raw[key], list):
            raise RolesError(f"{key} must be a list of column names") from None
    entities = tuple(_name(c, "an entity column") for c in raw["entities"])
    forbidden = tuple(_name(c, "a forbidden column") for c in raw["forbidden"])
    reporter = None if raw["reporter"] is None else _name(raw["reporter"], "reporter")
    roles = Roles(language=language, entities=entities, reporter=reporter, forbidden=forbidden, **single)
    names = roles.columns()
    if len(set(names)) != len(names):
        raise RolesError("a column takes at most one role") from None
    return roles


def load_roles(path: str | Path) -> Roles:
    try:
        obj = strict_load(Path(path).read_bytes())
    except OSError as exc:
        raise RolesError(f"cannot read {path} ({exc.__class__.__name__})") from None
    except StrictJsonError as err:
        raise RolesError(f"{path}: {err}") from None
    return roles_from_json(obj)


def check_roles(roles: Roles, export: Export) -> None:
    missing = [c for c in roles.columns() if not export.has(c)]
    if missing:
        raise RolesError(f"the export has no column {missing[0]!r} (a declared role)") from None


# --------------------------------------------------------------------------------------------------- dates

def date_part(value: str) -> str:
    """Rule 1.3: the part of a date value before its first space or ``T``."""
    return _DATE_CUT.split(value, maxsplit=1)[0]


def two_digit_year(yy: int) -> int:
    return 2000 + yy if yy < TWO_DIGIT_PIVOT else 1900 + yy


def parse_date(value: str | None, fmt: str, months: Sequence[str]) -> date | None:
    """``value`` as a calendar date in format ``fmt`` (one of :data:`DATE_FORMATS`), else None."""
    if not isinstance(value, str):
        return None
    m = _DATE_RE[fmt].fullmatch(date_part(value))
    if m is None:
        return None
    parts = m.groupdict()
    year = int(parts["y"]) if "y" in parts else two_digit_year(int(parts["yy"]))
    if "mon" in parts:
        mon = parts["mon"].lower()
        if mon not in months:
            return None
        month = months.index(mon) + 1
    else:
        month = int(parts["m"])
    try:
        return date(year, month, int(parts["d"]))
    except ValueError:
        return None


def format_shares(values: Sequence[str], months: Sequence[str]) -> dict[str, float]:
    """The share of ``values`` (non-empty strings) each format parses."""
    if not values:
        return {fmt: 0.0 for fmt in DATE_FORMATS}
    return {fmt: sum(parse_date(v, fmt, months) is not None for v in values) / len(values) for fmt in DATE_FORMATS}


def choose_date_format(values: Sequence[str], months: Sequence[str], min_share: float) -> str | None:
    """Rule 1.3: the first format that parses at least ``min_share`` of the values, else None."""
    if not values:
        return None
    n = len(values)
    for fmt in DATE_FORMATS:
        ok = sum(parse_date(v, fmt, months) is not None for v in values)
        if ok >= min_share * n:
            return fmt
    return None


# --------------------------------------------------------------------------------------------------- inference

@dataclass(frozen=True)
class Evidence:
    column: str
    position: int
    fill: float
    distinct: int
    values: int
    mean_tokens: float
    date_share: float
    letter_share: float
    header_words: tuple[str, ...]


def header_words(name: str) -> tuple[str, ...]:
    return tuple(w.lower() for w in _HEADER_SPLIT.split(name) if w)


def column_evidence(export: Export, rows: Iterable[int], months: Sequence[str]) -> list[Evidence]:
    """The evidence of rule 1.2 for every named column, over ``rows`` (indexes of the export's rows)."""
    rows = list(rows)
    n = len(rows)
    out = []
    for position, name in enumerate(export.columns):
        if not name:
            continue
        filled = 0
        values: list[str] = []
        for r in rows:
            cell = export.value(r, name)
            if cell is None:
                continue
            filled += 1
            values.extend(cell if isinstance(cell, tuple) else (cell,))
        k = len(values)
        shares = format_shares(values, months)
        out.append(Evidence(
            column=name, position=position, fill=filled / n if n else 0.0, distinct=len(set(values)), values=k,
            mean_tokens=sum(len(_TOKEN.findall(v)) for v in values) / k if k else 0.0,
            date_share=max(shares.values()) if k else 0.0,
            letter_share=sum(_LETTER.search(v) is not None for v in values) / k if k else 0.0,
            header_words=header_words(name)))
    return out


def infer_roles(evidence: Sequence[Evidence], params: Mapping[str, Any], site_words: Iterable[str],
                category_words: Iterable[str], rows: int) -> dict[str, str | None]:
    """Rule 1.2's five rules, in order, each over the columns no earlier rule took."""
    p = params["inference"]
    site_words, category_words = set(site_words), set(category_words)
    free = list(evidence)
    found: dict[str, str | None] = dict.fromkeys(SINGLE_ROLES)

    def take(role: str, ev: Evidence | None) -> None:
        if ev is not None:
            found[role] = ev.column
            free.remove(ev)

    def best(candidates: list[Evidence], key: Any) -> Evidence | None:
        """The candidate with the largest key; ties go to the leftmost."""
        if not candidates:
            return None
        return sorted(candidates, key=lambda e: (key(e), -e.position), reverse=True)[0]

    top = best(free, lambda e: e.mean_tokens)
    if top is not None and top.mean_tokens >= p["narrative_min_mean_tokens"] and top.fill >= p["narrative_min_fill"]:
        take("narrative", top)
    take("date", best([e for e in free if e.values and e.date_share >= p["date_min_share"]], lambda e: e.fill))
    ids = [e for e in free if e.fill >= p["record_id_min_fill"] and e.values and e.distinct == e.values]
    take("record_id", min(ids, key=lambda e: e.position) if ids else None)
    worded = [e for e in free if e.fill >= p["site_min_fill"] and e.distinct >= p["site_min_distinct"]
              and set(e.header_words) & site_words]
    if worded:
        take("site", best(worded, lambda e: e.distinct))
    else:
        take("site", best([e for e in free if e.fill >= p["site_min_fill"]
                           and e.mean_tokens <= p["site_fallback_max_mean_tokens"] and e.distinct < rows],
                          lambda e: e.distinct))
    labels = [e for e in free if e.fill >= p["label_min_fill"] and e.letter_share >= p["label_min_letter_share"]
              and p["label_min_distinct"] <= e.distinct <= p["label_max_distinct"]
              and e.mean_tokens <= p["label_max_mean_tokens"]]
    worded = [e for e in labels if set(e.header_words) & category_words]
    if worded:
        take("category", min(worded, key=lambda e: e.position))
    else:
        take("category", best(labels, lambda e: e.distinct))
    return found


def roles_right(inferred: Mapping[str, str | None], declared: Roles) -> int:
    """How many of the five declared single roles the inference found."""
    return sum(1 for r in SINGLE_ROLES if inferred.get(r) == getattr(declared, r))

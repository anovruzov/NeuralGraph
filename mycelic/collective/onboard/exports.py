"""Reading an export (rule 1.1, as D002 changed it): any CSV, pipe- or tab-delimited text with a header, or JSON lines.

* **Text.** The bytes are decoded as UTF-8, a leading byte-order mark dropped; if that fails, the whole file is
  decoded as Latin-1. A line ends at ``\\r\\n``, ``\\r`` or ``\\n`` (nothing else, so a Latin-1 ``\\x85`` stays text).
  An empty line is no row; :attr:`Export.blank_lines` counts them.
* **Format.** JSON lines when the first non-empty line starts with ``{`` (leading spaces aside). Otherwise delimited
  text: the header is the first non-empty line, and the delimiter is whichever of ``|``, tab and ``,`` occurs most
  often in it, ties in that order.
* **Splitting** (``docs/collective/onboard/CHOICE-D002.md``, change (a)). For every delimiter, each line is one row,
  the header line too. :func:`split_line` reads one line with Python's ``csv`` module, that delimiter and the module's
  default quoting (quote character ``"``; two quotes inside a quoted field stand for one), so a field in double
  quotes loses its quotes and a delimiter inside the quotes does not split it. Each line is read on its own: a quote
  it leaves open closes at its end and never takes in the next line. The module's limit on a field's length does not
  apply. A row with a different number of fields than the header is rejected as ``wrong_width``.
  ``tools/onboard/fetch_msha.py`` splits the accident file with the same function.
* **JSON lines.** The columns are the keys in the order they first appear. A line that is not JSON is
  ``invalid_json``, one that is not an object ``not_object``; an object value, or a list under a key without the
  ``[]`` suffix, is ``nested_value``. A number becomes its text, ``true``/``false`` their words, ``null`` absent.
* **Values.** Every value is stripped; an empty value is absent (None). A header ending in ``[]`` is a list column:
  its cell is split at ``;`` (a JSON list is taken as it is), each part stripped, empty parts and repeats dropped, and
  an empty list is absent.

Rows are kept in file order as tuples, one cell per column. Values are reached through :meth:`Export.value`, so a
caller (the drafter) can be shown to read only what it is allowed to (rule 1.3).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..jsonio import StrictJsonError, strict_load

BOM = b"\xef\xbb\xbf"
DELIMITERS = (("|", "pipe"), ("\t", "tab"), (",", "comma"))
JSON_LINES = "jsonl"
FORMATS = (JSON_LINES, *(name for _, name in DELIMITERS))
LIST_SUFFIX = "[]"
LIST_SEP = ";"
LINE_BREAK = re.compile(r"\r\n|\r|\n")
REJECT_REASONS = ("wrong_width", "invalid_json", "not_object", "nested_value")
# the csv module's default quoting, written out so that a later default cannot change how a line reads
QUOTING = {"quotechar": '"', "doublequote": True, "escapechar": None, "skipinitialspace": False, "strict": False,
           "quoting": csv.QUOTE_MINIMAL}

Cell = Any          # None (absent), str, or tuple[str, ...] for a list column


class ExportError(ValueError):
    """The file cannot be read as an export (no header, a repeated column name, an unreadable file)."""


@dataclass(frozen=True)
class Export:
    format: str
    encoding: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    rejected: Mapping[str, int]
    blank_lines: int

    def column_index(self, name: str) -> int:
        try:
            return self.columns.index(name)
        except ValueError:
            raise ExportError(f"the export has no column {name!r}") from None

    def has(self, name: str) -> bool:
        return name in self.columns

    def value(self, row: int, name: str) -> Cell:
        """The cell of ``row`` (an index into :attr:`rows`) in column ``name``."""
        return self.rows[row][self.column_index(name)]

    def __len__(self) -> int:
        return len(self.rows)


def is_list_column(name: str) -> bool:
    return name.endswith(LIST_SUFFIX)


def decode(data: bytes) -> tuple[str, str]:
    """Rule 1.1, text: UTF-8 without a leading byte-order mark, else the whole file as Latin-1."""
    if data.startswith(BOM):
        data = data[len(BOM):]
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def split_lines(text: str) -> list[str]:
    """The lines of ``text`` without their ends; a final line end does not make an empty last line."""
    lines = LINE_BREAK.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def choose_delimiter(header: str) -> tuple[str, str]:
    """Rule 1.1: the delimiter occurring most often in the header line, ties in the order pipe, tab, comma."""
    best = DELIMITERS[0]
    for delim in DELIMITERS[1:]:
        if header.count(delim[0]) > header.count(best[0]):
            best = delim
    return best


def _read_line(line: str, delim: str) -> list[str]:
    return next(csv.reader((line,), delimiter=delim, **QUOTING), [])


def split_line(line: str, delim: str) -> list[str]:
    """The fields of one line (rule 1.1 as D002 changed it): Python's ``csv`` module with ``delim`` and its default
    quoting, the line read on its own. A field in double quotes loses its quotes; a delimiter inside them does not
    split; two quotes inside a quoted field stand for one; a quote the line leaves open closes at its end. The
    module's field-length limit is raised for this line when it is too short to hold the line, and put back after.
    An empty line has no field. Values are returned as read, not stripped."""
    limit = csv.field_size_limit()
    if len(line) < limit:
        return _read_line(line, delim)
    csv.field_size_limit(len(line) + 1)
    try:
        return _read_line(line, delim)
    finally:
        csv.field_size_limit(limit)


def _scalar(value: str) -> str | None:
    s = value.strip()
    return s or None


def _list_cell(parts: Sequence[str]) -> tuple[str, ...] | None:
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if s and s not in out:
            out.append(s)
    return tuple(out) or None


def _cell(name: str, raw: str) -> Cell:
    if is_list_column(name):
        return _list_cell(raw.split(LIST_SEP))
    return _scalar(raw)


def _header(names: Sequence[str]) -> tuple[str, ...]:
    columns = tuple(n.strip() for n in names)
    named = [c for c in columns if c]
    if not named:
        raise ExportError("the header names no column")
    if len(set(named)) != len(named):
        raise ExportError("the header repeats a column name")
    return columns


def _delimited(lines: list[str], delim: str) -> tuple[tuple[str, ...], list[tuple[Cell, ...]], dict[str, int], int]:
    blank = 0
    start = 0
    while start < len(lines) and lines[start] == "":
        blank += 1
        start += 1
    if start == len(lines):
        raise ExportError("the export has no header line")
    columns = _header(split_line(lines[start], delim))
    rows: list[tuple[Cell, ...]] = []
    rejected: dict[str, int] = {}
    for line in lines[start + 1:]:
        if line == "":
            blank += 1
            continue
        fields = split_line(line, delim)
        if len(fields) != len(columns):
            rejected["wrong_width"] = rejected.get("wrong_width", 0) + 1
            continue
        rows.append(tuple(_cell(name, raw) for name, raw in zip(columns, fields)))
    return columns, rows, rejected, blank


def _json_value(name: str, value: Any) -> Cell:
    """A JSON value as a cell; raises ValueError for a nested value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _cell(name, value)
    if isinstance(value, list) and is_list_column(name):
        parts = []
        for v in value:
            c = _json_value("", v)
            if c is not None:
                parts.append(c)
        return _list_cell(parts)
    raise ValueError("nested value")


def _json_lines(lines: list[str]) -> tuple[tuple[str, ...], list[tuple[Cell, ...]], dict[str, int], int]:
    columns: list[str] = []
    objects: list[dict[str, Cell]] = []
    rejected: dict[str, int] = {}
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            continue
        try:
            obj = strict_load(line)
        except StrictJsonError:
            rejected["invalid_json"] = rejected.get("invalid_json", 0) + 1
            continue
        if not isinstance(obj, dict):
            rejected["not_object"] = rejected.get("not_object", 0) + 1
            continue
        try:
            cells = {k.strip(): _json_value(k.strip(), obj[k]) for k in obj}
        except ValueError:
            rejected["nested_value"] = rejected.get("nested_value", 0) + 1
            continue
        for k in cells:
            if k not in columns:
                columns.append(k)
        objects.append(cells)
    if not columns:
        raise ExportError("the JSON lines name no column")
    header = _header(columns)
    rows = [tuple(o.get(c) for c in header) for o in objects]
    return header, rows, rejected, blank


def parse_export(data: bytes) -> Export:
    """Rule 1.1 over the bytes of one export."""
    text, encoding = decode(data)
    lines = split_lines(text)
    first = next((line for line in lines if line.strip()), None)
    if first is None:
        raise ExportError("the export is empty")
    if first.lstrip().startswith("{"):
        fmt = JSON_LINES
        columns, rows, rejected, blank = _json_lines(lines)
    else:
        delim, fmt = choose_delimiter(first)
        columns, rows, rejected, blank = _delimited(lines, delim)
    return Export(format=fmt, encoding=encoding, columns=columns, rows=tuple(rows),
                  rejected=MappingProxyType(dict(sorted(rejected.items()))), blank_lines=blank)


def read_export(path: str | Path) -> Export:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ExportError(f"cannot read {path} ({exc.__class__.__name__})") from None
    return parse_export(data)

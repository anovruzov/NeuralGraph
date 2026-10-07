#!/usr/bin/env python
"""The number lint (G8): the build fails when a number on screen cannot be traced to a primary run file.

    python demo/collective/lint_numbers.py RUN_DIR [--console PATH] [--script PATH] [--readme PATH] [--dry-run]

STRATEGY section 9.1: every number on screen is read from a run file, and a lint fails the build if one is not. This
lint reads the six run files of ``RUN_DIR`` and the console, the talk track (``SCRIPT.md``) and ``README.md`` next to
it (or the paths given), and prints one line per violation, ``lint: <file>: <rule>: <token>``:

* ``schema``: ``screen.json`` breaks ``SCREEN_SCHEMA`` or its part rules; ``run_id_mismatch``: its run id is not the
  scorecard's;
* ``src_not_primary``, ``src_unresolved``, ``src_not_scalar``: an item's source is not a primary run file, points
  nowhere, or points at something other than a scalar (``runfiles.resolve``);
* ``display_mismatch``: an item's display is not ``format_value`` of its source, or does not read back
  (``parse_display``) to the source's value;
* ``digit_in_text`` and ``number_word``: a digit (after removing the identifiers in :data:`IDENTIFIERS`) or a number
  word in a static text of the screen (text parts, item labels, beat titles, control labels), in the console's visible
  text, ``<title>``, ``alt``, ``placeholder`` or ``aria-*`` attributes, in a string literal of the console's script
  (escapes decoded; CSS lengths, durations, colours and colour or transform functions allowed), or in a line of
  ``SCRIPT.md`` (the first cell of a table row, the beat time, and separator rows excepted);
* ``console_markup``: an ordered list, a progress or meter element, a CSS counter or a decimal list style, which render
  digits that are in no run file;
* ``aggregate_metric_key``: a key or item id of ``screen.json`` with a segment naming an aggregate metric;
* ``denylisted_phrase`` and ``benchmark_figure`` (STRATEGY section 9.1), in ``screen.json``, the console,
  ``SCRIPT.md`` and ``README.md``.

Exit 0 with ``lint: ok (<n> items, <m> texts checked)``; 1 with any violation; 2 with one ``error:`` line for a
missing directory or file, invalid JSON, or run files of another schema version. Pure: it reads files and writes
nothing; it imports only ``screen``, ``runfiles``, ``jsonio``, ``schemacheck`` and ``html.parser``.
"""
from __future__ import annotations

import argparse
import html.parser
import re
import sys
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from demo.collective import screen as scr  # noqa: E402
from mycelic.collective import runfiles  # noqa: E402
from mycelic.collective.jsonio import StrictJsonError  # noqa: E402

CLI = "demo.collective.lint_numbers"
SCHEMA_VERSION = 1
IDENTIFIERS = ("X1", "X2", "X4", "X5", "X6", "E1", "E2", "E3", "E5", "N1", "G0", "T0", "T1", "T2", "T3", "Phase-1",
               "Phase-2", "60-second")
NUMBER_WORDS = ("zero", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "twenty", "thirty", "forty", "fifty", "hundred", "thousand", "million", "billion", "dozen", "percent")
METRIC_SEGMENTS = frozenset({"recall", "precision", "lift", "ap", "f1", "auc", "accuracy"})
DENYLIST = ("collective intelligence", "any field(s)", "shared memory for agents", "no model in the data path",
            "learns", "learning", "self-improving")          # '(s)': with or without a plural s
BENCHMARK_FIGURES = (r"57\s*/\s*78", r"78\s*/\s*57", r"57\s*%\s*vs\.?\s*78\s*%", r"78\s*%\s*vs\.?\s*57\s*%",
                     r"75\.1\s*%", r"(?<![0-9.])0\s*%\s*centrali[sz]ed", r"(?<![0-9.])2\.6\s*[×x]")
FORBIDDEN_TAGS = ("ol", "progress", "meter")
TEXT_ATTRIBUTES = ("title", "alt", "placeholder")
JSON_FILES = ("scorecard.json", "trace.json", "leakage.json", "screen.json")

_IDENT = re.compile(r"(?<![A-Za-z0-9_-])(?:" + "|".join(re.escape(i) for i in IDENTIFIERS) + r")(?![A-Za-z0-9_-])")
_NUMBER_WORD = re.compile(r"\b(?:" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)
_DENY = re.compile(r"\b(?:" + "|".join(r"\s+".join(re.escape(w).replace(r"\(s\)", "s?") for w in p.split())
                                       for p in DENYLIST) + r")\b", re.IGNORECASE)
_BENCH = re.compile("|".join(BENCHMARK_FIGURES), re.IGNORECASE)
_MARKUP = re.compile(r"<(?:ol|progress|meter)\b|\bcounters?\s*\(|list-style(?:-type)?\s*:[^;\"'}]*\bdecimal",
                     re.IGNORECASE)
_CSS_FUNCTION = re.compile(r"\b(?:rgba?|hsla?|translate(?:[XYZ]|3d)?|scale(?:[XYZ]|3d)?|rotate(?:[XYZ]|3d)?|skew[XY]?"
                           r"|matrix(?:3d)?|cubic-bezier|calc|var)\([^()]*\)")
_CSS_TOKEN = re.compile(r"-?[0-9]*\.?[0-9]+(?:px|rem|em|%|vh|vw|vmin|vmax|ms|s|fr|ch|deg)|#[0-9a-fA-F]{3,8}")
_SEPARATOR_ROW = re.compile(r"\|?[\s:|-]+\|?")
_DIGIT = re.compile(r"[0-9]")


class LintError(Exception):
    """A usage or input problem: one line, exit 2."""


# --------------------------------------------------------------------------------------------------- text rules

def text_problems(text: str, *, literal: bool = False) -> list[tuple[str, str]]:
    """``(rule, token)`` for a digit left after removing the identifiers (and, in a script literal, CSS lengths,
    durations, colours and colour or transform functions) and for each number word."""
    out = []
    rest = _IDENT.sub(" ", text)
    if literal:
        rest = _CSS_FUNCTION.sub(" ", rest)
        rest = " ".join(t for t in re.split(r"[\s,;]+", rest) if _CSS_TOKEN.fullmatch(t.rstrip(":")) is None)
    for token in re.split(r"\s+", rest):
        if _DIGIT.search(token):
            out.append(("digit_in_text", token))
            break
    out += [("number_word", m.group()) for m in _NUMBER_WORD.finditer(text)]
    return out


def phrase_problems(text: str) -> list[tuple[str, str]]:
    return ([("denylisted_phrase", m.group()) for m in _DENY.finditer(text)]
            + [("benchmark_figure", m.group()) for m in _BENCH.finditer(text)])


# --------------------------------------------------------------------------------------------------- the console

def _decode(raw: str) -> str:
    """JavaScript string escapes decoded: \\uXXXX, \\u{...}, \\xXX and the single-character escapes."""
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def one(m: re.Match[str]) -> str:
        body = m.group(1)
        if body.startswith("u{"):
            return chr(int(body[2:-1], 16))
        if body.startswith("u") or body.startswith("x"):
            return chr(int(body[1:], 16))
        return simple.get(body, body)

    return re.sub(r"\\(u\{[0-9A-Fa-f]{1,6}\}|u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2}|.)", one, raw, flags=re.DOTALL)


def script_literals(source: str) -> Iterator[str]:
    """The decoded string literals of a script: single- and double-quoted strings and the text parts of template
    literals (``${...}`` expressions are scanned for literals of their own). Comments are skipped."""
    i, n = 0, len(source)
    stack: list[int] = []            # brace depth of each open template expression
    depth = 0
    while i < n:
        c = source[i]
        if c == "/" and source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and source.startswith("/*", i):
            j = source.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"":
            j, buf = i + 1, []
            while j < n and source[j] != c:
                if source[j] == "\\":
                    buf.append(source[j:j + 2])
                    j += 2
                else:
                    buf.append(source[j])
                    j += 1
            yield _decode("".join(buf))
            i = j + 1
        elif c == "`" or (c == "}" and stack and depth == stack[-1]):
            if c == "}":
                stack.pop()
            j, buf = i + 1, []
            while j < n and source[j] != "`" and not source.startswith("${", j):
                if source[j] == "\\":
                    buf.append(source[j:j + 2])
                    j += 2
                else:
                    buf.append(source[j])
                    j += 1
            yield _decode("".join(buf))
            if source.startswith("${", j):
                stack.append(depth)
                i = j + 2
            else:
                i = j + 1
        else:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1


class _Console(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.scripts: list[str] = []
        self.tags: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        if tag in ("script", "style"):
            self._in = tag
        for name, value in attrs:
            if value is not None and (name in TEXT_ATTRIBUTES or name.startswith("aria-")):
                self.texts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._in:
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in == "script":
            self.scripts.append(data)
        elif self._in is None and data.strip():
            self.texts.append(data)


# --------------------------------------------------------------------------------------------------- the lint

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LintError(f"{path}: missing") from None
    except (OSError, UnicodeDecodeError):
        raise LintError(f"{path}: unreadable") from None


def read_docs(directory: Path) -> dict[str, Any]:
    if not directory.is_dir():
        raise LintError(f"{directory}: not a run directory")
    try:
        docs = runfiles.read_run_files(directory, runfiles.RUN_FILES)
    except runfiles.RunFileError as exc:
        raise LintError(f"{directory}: {exc.where} is {exc.problem}") from None
    for name in JSON_FILES:
        doc = docs[name]
        version = doc.get("schema_version") if isinstance(doc, dict) else None
        if version != SCHEMA_VERSION:
            raise LintError(f"{directory}: run files are schema version {version}; this engine reads "
                            f"{SCHEMA_VERSION}; record a new run with --record")
    return docs


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key in value:
            yield key
            yield from _keys(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _script_lines(text: str) -> Iterator[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            if _SEPARATOR_ROW.fullmatch(stripped):
                continue
            cells = stripped.strip("|").split("|")
            yield "|".join(cells[1:])
        else:
            yield line


def lint(directory: Path, *, console: Path, script: Path, readme: Path) -> tuple[list[str], int, int]:
    """``(violation lines, items, texts checked)``; :class:`LintError` for a usage or input problem."""
    docs = read_docs(directory)
    console_text, script_text, readme_text = _read(console), _read(script), _read(readme)
    screen = docs["screen.json"]
    primary = {name: docs[name] for name in runfiles.PRIMARY_FILES}
    out: list[str] = []

    def add(file: str, rule: str, token: Any) -> None:
        out.append(f"lint: {file}: {rule}: {token}")

    problems = scr.screen_problems(screen)
    for path, keyword in problems:
        add("screen.json", "schema", f"{path} {keyword}")
    if problems:
        return out, 0, 0
    if screen["run_id"] != docs["scorecard.json"].get("run_id"):
        add("screen.json", "run_id_mismatch", screen["run_id"])
    for item in screen["items"]:
        try:
            value = runfiles.resolve(primary, item["src"])
        except runfiles.RunFileError as exc:
            add("screen.json", exc.problem, f"{item['id']} {item['src']}")
            continue
        try:
            ok = (scr.format_value(value, item["fmt"]) == item["display"]
                  and scr.parse_display(item["display"], item["fmt"]) == scr.comparable(value, item["fmt"]))
        except scr.ScreenError:
            ok = False
        if not ok:
            add("screen.json", "display_mismatch", f"{item['id']}={item['display']}")
    texts = [p["text"] for b in screen["blocks"] for p in b["parts"] if p["text"] is not None]
    texts += [i["label"] for i in screen["items"]] + [b["title"] for b in screen["beats"]]
    texts += [c["label"] for c in screen["controls"]]
    checked = len(texts)
    for text in texts:
        for rule, token in text_problems(text):
            add("screen.json", rule, token)
    for key in sorted(set(_keys(screen)) | {i["id"] for i in screen["items"]}):
        if METRIC_SEGMENTS & set(re.split(r"[_.\-]", key.lower())):
            add("screen.json", "aggregate_metric_key", key)
    parser = _Console()
    parser.feed(console_text)
    parser.close()
    for text in parser.texts:
        checked += 1
        for rule, token in text_problems(text):
            add(console.name, rule, token)
    for literal in (lit for source in parser.scripts for lit in script_literals(source)):
        checked += 1
        for rule, token in text_problems(literal, literal=True):
            add(console.name, rule, token)
        if literal.strip().lower() in FORBIDDEN_TAGS:
            add(console.name, "console_markup", literal.strip())
    for m in _MARKUP.finditer(console_text):
        add(console.name, "console_markup", m.group())
    for line in _script_lines(script_text):
        checked += 1
        for rule, token in text_problems(line):
            add(script.name, rule, token)
    for file, text in (("screen.json", (directory / "screen.json").read_text(encoding="utf-8")),
                       (console.name, console_text), (script.name, script_text), (readme.name, readme_text)):
        for rule, token in phrase_problems(text):
            add(file, rule, token)
    return out, len(screen["items"]), checked


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python demo/collective/lint_numbers.py",
                                description="Fail when a number on screen cannot be traced to a primary run file.")
    p.add_argument("run_dir", metavar="RUN_DIR", help="a run directory holding the six run files")
    p.add_argument("--console", default=str(HERE / "console.html"))
    p.add_argument("--script", default=str(HERE / "SCRIPT.md"))
    p.add_argument("--readme", default=str(HERE / "README.md"))
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.dry_run:
            print(f"dry-run: {CLI}")
            if not Path(args.run_dir).is_dir():
                print(f"would need: run directory {args.run_dir}")
            else:
                read_docs(Path(args.run_dir))
            for path in (args.console, args.script, args.readme):
                if not Path(path).is_file():
                    print(f"would need: {path}")
            return 0
        lines, items, texts = lint(Path(args.run_dir), console=Path(args.console), script=Path(args.script),
                                   readme=Path(args.readme))
    except LintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, StrictJsonError):
        print(f"error: {args.run_dir}: unreadable", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    if lines:
        return 1
    print(f"lint: ok ({items} items, {texts} texts checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

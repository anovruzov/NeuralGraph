"""Deterministic canonicalisation of entity ids and phrases. Never a model.

Two text folds, both length- and offset-preserving except for the characters they delete or merge:

* :func:`normalise` folds full-width ASCII (U+FF01..U+FF5E) to ASCII and deletes zero-width characters. Nothing
  else: no NFKC, so superscripts, circled digits, ligatures and ``ß`` stay as written.
* :func:`fold_phrase` normalises, writes every hyphen-like character and ``_`` as ``-``, collapses each whitespace
  run (NBSP included) to one space and lower-cases each code point whose lower case is one code point (``İ`` stays,
  ``Ü`` becomes ``ü``). Aliases, lexicon terms, negation cues and grounding all compare folded phrases.

Both return an index map: ``index[i]`` is the original offset of folded character ``i``, plus a final sentinel, so
every span reported here indexes the ORIGINAL text.

An entity type either has an id format (a list of segments compiled by :func:`compile_id_format`) or is alias-only
(a closed list of ids reachable only through the pack's alias table). The id-format rules, in order:

1. full-width fold; 2. separators at separator positions (``-``, ``_``, every hyphen-like character, a space or
NBSP, and the type's own separator); 3. zero-width deletion; 4. ASCII-only case folding to the type's case
(``ß``, ``İ`` and ``ı`` are never folded); 5. leftmost-longest matching bounded on both sides by
non-alphanumerics, and never followed by a hard separator plus an alphanumeric (so ``AB-9-C`` is rejected
outright); 6. a lookalike match that holds a non-ASCII letter or digit is not resolved but counted, as
``non_ascii_digit`` when a non-ASCII character sits in a digit position and ``homoglyph`` otherwise; 7. a form
separated by a space or NBSP resolves only to an id the site already knows (alias targets plus its master data),
else it is counted as ``space_unknown``; 8. alias lookup over folded phrases.

``ALNUM(c)`` is ``re.fullmatch(r"[^\\W_]", c)``: every boundary decision here and in grounding uses it, so a Unicode
letter or digit next to an id blocks the match just as an ASCII one does.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    from .loader import FrozenPack

HYPHEN_LIKES = "".join(chr(c) for c in range(0x2010, 0x2016)) + "\u2212\ufe63\uff0d"
ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
SPACE_SEPS = " \u00a0"
HARD_SEPS = "-_" + HYPHEN_LIKES
SEGMENT_KINDS = ("alpha", "digit", "alnum", "literal", "sep")
SEP_VALUES = ("required", "optional")
CASES = ("upper", "lower")
TYPE_SEPARATORS = ("-", "/")
UNRESOLVED_KEYS = ("homoglyph", "non_ascii_digit", "space_unknown")
METHODS = ("exact", "variant", "alias")
MAX_SEGMENT = 20
MAX_LITERAL = 8
MAX_ID_LENGTH = 40

_ALNUM = re.compile(r"[^\W_]")
_DIGIT = re.compile(r"\d")
_LITERAL = {"upper": re.compile(r"[A-Z0-9]{1,8}", re.ASCII), "lower": re.compile(r"[a-z0-9]{1,8}", re.ASCII)}
_NEEDS_NORMALISE = re.compile("[" + ZERO_WIDTH + "\uff01-\uff5e]")
_TO_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)
_TO_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_SENTENCE_END = re.compile(r"[.!?;](?=\s|$)|\n")
_BEFORE = r"(?<![^\W_])"
_AFTER = r"(?![^\W_])"


def is_alnum(c: str) -> bool:
    return _ALNUM.fullmatch(c) is not None


def ascii_case(text: str, case: str) -> str:
    """ASCII letters only, to ``upper`` or ``lower``; every other character (``ß``, ``İ``, ``ı``) is left alone."""
    return text.translate(_TO_UPPER if case == "upper" else _TO_LOWER)


def normalise(text: str) -> tuple[str, tuple[int, ...]]:
    if _NEEDS_NORMALISE.search(text) is None:
        return text, tuple(range(len(text) + 1))
    out: list[str] = []
    index: list[int] = []
    for i, c in enumerate(text):
        if c in ZERO_WIDTH:
            continue
        o = ord(c)
        out.append(chr(o - 0xFEE0) if 0xFF01 <= o <= 0xFF5E else c)
        index.append(i)
    index.append(len(text))
    return "".join(out), tuple(index)


def fold_phrase(text: str) -> tuple[str, tuple[int, ...]]:
    norm, idx = normalise(text)
    out: list[str] = []
    index: list[int] = []
    in_space = False
    for i, c in enumerate(norm):
        if c.isspace():
            if not in_space:
                out.append(" ")
                index.append(idx[i])
            in_space = True
            continue
        in_space = False
        if c in HYPHEN_LIKES or c == "_":
            c = "-"
        else:
            low = c.lower()
            if len(low) == 1:
                c = low
        out.append(c)
        index.append(idx[i])
    index.append(idx[-1])
    return "".join(out), tuple(index)


def folded(text: str) -> str:
    """``fold_phrase(text)`` without the index, stripped: the form aliases, terms and person values compare in."""
    return fold_phrase(text)[0].strip()


def term_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    """One word-bounded alternation over already-folded terms, longest first (then by text), so the leftmost match
    is also the longest at its start. A boundary is required only at a term edge that is alphanumeric, so a
    punctuation term such as ``,`` still matches next to a word. None for no terms."""
    words, others = [], []
    for term in sorted(set(terms), key=lambda t: (-len(t), t)):
        body = re.escape(term) + (_AFTER if is_alnum(term[-1]) else "")
        (words if is_alnum(term[0]) else others).append(body)
    if not words and not others:
        return None
    parts = []
    if words:
        parts.append(_BEFORE + "(?:" + "|".join(words) + ")")
    parts.extend(others)
    return re.compile("|".join(parts))


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans of ``text``: split after ``[.!?;]`` followed by whitespace or the end, and at newlines;
    blank pieces are skipped."""
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        if text[start:m.end()].strip():
            spans.append((start, m.end()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def find_bounded(needle: str, haystack: str) -> bool:
    """True when ``needle`` occurs in ``haystack`` with ALNUM boundaries at its alphanumeric edges."""
    if not needle:
        return False
    pattern = term_regex([needle])
    return pattern is not None and pattern.search(haystack) is not None


# --------------------------------------------------------------------------------------------------- id formats

class IdFormatError(ValueError):
    """A bad id format; ``index`` is the offending segment (None for the format as a whole)."""

    def __init__(self, index: int | None, problem: str) -> None:
        super().__init__(problem if index is None else f"segment {index}: {problem}")
        self.index = index
        self.problem = problem


@dataclass(frozen=True)
class IdFormat:
    """``segments`` are ``(kind, value)`` pairs. ``pattern`` is the matching pattern (ASCII text, named groups
    ``g<i>`` and ``s<i>``, separator variants allowed); ``scanner`` and ``shadow`` add the boundary rules for
    finding ids in text, ``shadow`` with Unicode letter and digit classes; ``canonical`` matches canonical ids only."""

    segments: tuple[tuple[str, Any], ...]
    pattern: re.Pattern[str]
    scanner: re.Pattern[str]
    shadow: re.Pattern[str]
    canonical: re.Pattern[str]
    case: str
    separator: str | None
    strip_leading_zeros: bool

    def canonical_of(self, match: re.Match[str]) -> str:
        parts = []
        for i, (kind, value) in enumerate(self.segments):
            if kind == "sep":
                parts.append(self.separator or "")
                continue
            group = ascii_case(match.group(f"g{i}") or "", self.case)
            if kind == "digit" and self.strip_leading_zeros and group:
                group = group.lstrip("0") or "0"
            parts.append(group)
        return "".join(parts)

    def canonical_form(self, text: str) -> str | None:
        """The canonical id ``text`` spells (after ASCII case folding), or None if it does not fullmatch."""
        m = self.pattern.fullmatch(ascii_case(text, self.case))
        return self.canonical_of(m) if m is not None else None

    def is_canonical(self, text: Any) -> bool:
        return isinstance(text, str) and self.canonical_form(text) == text

    def space_form(self, match: re.Match[str]) -> bool:
        return any(kind == "sep" and (match.group(f"s{i}") or "") in tuple(SPACE_SEPS)
                   for i, (kind, _) in enumerate(self.segments))

    def non_ascii_kind(self, match: re.Match[str]) -> str | None:
        """For a shadow match: ``non_ascii_digit``, ``homoglyph`` or None when no segment holds a non-ASCII character
        (separators may legitimately be non-ASCII hyphens or NBSP)."""
        found = None
        for i, (kind, value) in enumerate(self.segments):
            if kind == "sep":
                continue
            for pos, ch in enumerate(match.group(f"g{i}") or ""):
                if ord(ch) < 128:
                    continue
                digit_position = (kind == "digit" or (kind == "literal" and value[pos].isdigit())
                                  or (kind == "alnum" and _DIGIT.fullmatch(ch) is not None))
                if digit_position:
                    return "non_ascii_digit"
                found = "homoglyph"
        return found


def _char_class(chars: str) -> str:
    out = []
    for c in sorted(set(chars)):
        out.append(re.escape(c) if ord(c) < 128 else f"\\u{ord(c):04x}")
    return "[" + "".join(out) + "]"


def _classes(kind: str, value: Any) -> set[str]:
    if kind == "alpha":
        return {"alpha"}
    if kind == "digit":
        return {"digit"}
    if kind == "alnum":
        return {"alpha", "digit"}
    return {"digit" if c.isdigit() else "alpha" for c in value}


def _range(i: int, value: Any) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
        raise IdFormatError(i, "range must be [min, max] integers") from None
    lo, hi = value
    if not (0 <= lo <= hi <= MAX_SEGMENT and hi >= 1):
        raise IdFormatError(i, f"range must have 0 <= min <= max <= {MAX_SEGMENT} and max >= 1") from None
    return lo, hi


def check_segments(segments: Any, case: str, *, separators: Sequence[str] = TYPE_SEPARATORS,
                   separator: str | None) -> tuple[tuple[str, Any], ...]:
    """Structural checks shared by id formats and person generators: kinds, ranges, literals in the case, seps
    not at an edge and not adjacent, a separator exactly when a sep segment exists, a non-empty minimum."""
    if not isinstance(segments, list) or not segments:
        raise IdFormatError(None, "id_format must be a non-empty list of segments") from None
    out: list[tuple[str, Any]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or len(seg) != 1 or next(iter(seg)) not in SEGMENT_KINDS:
            raise IdFormatError(i, "unknown segment kind (one of alpha, digit, alnum, literal, sep)") from None
        kind, value = next(iter(seg.items()))
        if kind in ("alpha", "digit", "alnum"):
            out.append((kind, _range(i, value)))
        elif kind == "literal":
            if not isinstance(value, str) or _LITERAL[case].fullmatch(value) is None:
                raise IdFormatError(i, f"literal must be 1..{MAX_LITERAL} ASCII letters or digits in the type's "
                                       "case") from None
            out.append((kind, value))
        else:
            if value not in SEP_VALUES:
                raise IdFormatError(i, "sep must be required or optional") from None
            out.append((kind, value))
    if out[0][0] == "sep" or out[-1][0] == "sep":
        raise IdFormatError(0 if out[0][0] == "sep" else len(out) - 1, "sep at the edge of the format") from None
    for i in range(1, len(out)):
        if out[i][0] == "sep" and out[i - 1][0] == "sep":
            raise IdFormatError(i, "adjacent seps") from None
    has_sep = any(kind == "sep" for kind, _ in out)
    if has_sep and separator not in separators:
        raise IdFormatError(None, "a sep segment needs a separator") from None
    if not has_sep and separator is not None:
        raise IdFormatError(None, "separator without a sep segment") from None
    if sum(_min_len(kind, value) for kind, value in out if kind != "sep") < 1:
        raise IdFormatError(None, "the format admits the empty string") from None
    return tuple(out)


def _min_len(kind: str, value: Any) -> int:
    return len(value) if kind == "literal" else value[0]


def _max_len(kind: str, value: Any) -> int:
    return len(value) if kind == "literal" else 1 if kind == "sep" else value[1]


def _fragment(kind: str, value: Any, case: str, *, shadow: bool) -> str:
    letters = "A-Z" if case == "upper" else "a-z"
    if kind == "literal":
        if not shadow:
            return re.escape(value)
        return "".join(r"\d" if c.isdigit() else r"[^\W\d_]" for c in value)
    lo, hi = value
    rep = f"{{{lo},{hi}}}"
    if kind == "alpha":
        return (r"[^\W\d_]" if shadow else f"[{letters}]") + rep
    if kind == "digit":
        return (r"\d" if shadow else "[0-9]") + rep
    return (r"[^\W_]" if shadow else f"[{letters}0-9]") + rep


def compile_id_format(type_id: str, segments: Any, case: str, separator: str | None,
                      strip_leading_zeros: bool) -> IdFormat:
    if case not in CASES:
        raise IdFormatError(None, "case must be upper or lower") from None
    segs = check_segments(segments, case, separator=separator)
    # adjacency: two non-sep segments that may touch (only optional seps or possibly-empty segments between them)
    # must not share a character class, or segmentation would not be unique
    for j, (kind_j, value_j) in enumerate(segs):
        if kind_j == "sep":
            continue
        for i in range(j - 1, -1, -1):
            kind_i, value_i = segs[i]
            if kind_i == "sep":
                if value_i == "required":
                    break
                continue
            if _classes(kind_i, value_i) & _classes(kind_j, value_j):
                raise IdFormatError(j, "ambiguous adjacent segments") from None
            if _min_len(kind_i, value_i) > 0:
                break
    if sum(_max_len(kind, value) for kind, value in segs) > MAX_ID_LENGTH:
        raise IdFormatError(None, f"ids may be longer than {MAX_ID_LENGTH} characters") from None
    if strip_leading_zeros:
        for i, (kind, value) in enumerate(segs):
            if kind == "digit" and value[0] > 1:
                raise IdFormatError(i, "strip_leading_zeros needs every digit segment to have min <= 1") from None

    sep_class = _char_class(HARD_SEPS + SPACE_SEPS + (separator or ""))
    hard_class = _char_class(HARD_SEPS + (separator or ""))
    plain, shadow, canonical = [], [], []
    for i, (kind, value) in enumerate(segs):
        if kind == "sep":
            opt = "?" if value == "optional" else ""
            plain.append(f"(?P<s{i}>{sep_class}){opt}")
            shadow.append(f"(?P<s{i}>{sep_class}){opt}")
            canonical.append(re.escape(separator or ""))
            continue
        plain.append(f"(?P<g{i}>{_fragment(kind, value, case, shadow=False)})")
        shadow.append(f"(?P<g{i}>{_fragment(kind, value, case, shadow=True)})")
        if kind == "digit" and strip_leading_zeros:
            lo, hi = value
            body = "(?:0|[1-9][0-9]{0," + str(hi - 1) + "})"
            canonical.append(body + ("?" if lo == 0 else ""))
        else:
            canonical.append(_fragment(kind, value, case, shadow=False))
    bounds = (_BEFORE + "(?:{})" + _AFTER + f"(?!{hard_class}[^\\W_])")
    pattern_text = "".join(plain)
    return IdFormat(
        segments=segs,
        pattern=re.compile(pattern_text, re.ASCII),
        scanner=re.compile(bounds.format(pattern_text)),
        shadow=re.compile(bounds.format("".join(shadow))),
        canonical=re.compile("".join(canonical), re.ASCII),
        case=case, separator=separator, strip_leading_zeros=bool(strip_leading_zeros),
    )


# --------------------------------------------------------------------------------------------------- canonicaliser

@dataclass(frozen=True)
class Mention:
    entity_type: str
    entity_id: str
    start: int
    end: int
    text: str
    method: str
    res_conf: float


@dataclass(frozen=True)
class ScanResult:
    mentions: tuple[Mention, ...]
    unresolved: Mapping[str, int]


class Canonicaliser:
    """Resolves entity mentions in text for one pack. ``known`` is the site's master data (world data, not pack
    data): known ids are the alias targets of each type plus these values. A non-canonical known value is refused."""

    def __init__(self, pack: "FrozenPack", known: Mapping[str, Iterable[str]] | None = None) -> None:
        self.pack = pack
        self.types = tuple(sorted(pack.entity_types))
        known = known or {}
        for t in sorted(known):
            if t not in pack.entity_types:
                raise ValueError(f"known ids for unknown entity type {t!r}") from None
        targets = pack.alias_targets()
        known_ids: dict[str, frozenset[str]] = {}
        for t in self.types:
            ids = set(targets.get(t, frozenset()))
            for value in known.get(t, ()):
                if not self.is_canonical(t, value):
                    raise ValueError(f"known id for {t} is not canonical") from None
                ids.add(value)
            known_ids[t] = frozenset(ids)
        self.known_ids: Mapping[str, frozenset[str]] = MappingProxyType(known_ids)
        conf = pack.extraction
        self._conf = {"exact": conf.confidence_exact, "variant": conf.confidence_variant,
                      "alias": conf.confidence_alias}
        self._alias: dict[str, tuple[re.Pattern[str], dict[str, str]]] = {}
        for t in self.types:
            table = {folded(alias): target for alias, target in pack.aliases.get(t, {}).items()}
            regex = term_regex(table)
            if regex is not None:
                self._alias[t] = (regex, table)

    def is_canonical(self, entity_type: str, value: Any) -> bool:
        et = self.pack.entity_types.get(entity_type)
        if et is None or not isinstance(value, str):
            return False
        if et.id_format is None:
            return value in et.ids
        return et.id_format.is_canonical(value)

    def is_known(self, entity_type: str, entity_id: str) -> bool:
        return entity_id in self.known_ids.get(entity_type, frozenset())

    def scan(self, text: str, types: Sequence[str] | None = None) -> ScanResult:
        norm, idx = normalise(text)
        unresolved = dict.fromkeys(UNRESOLVED_KEYS, 0)
        phrase: tuple[str, tuple[int, ...]] | None = None
        mentions: list[Mention] = []
        for t in (self.types if types is None else types):
            et = self.pack.entity_types[t]
            candidates: list[Mention] = []
            fmt = et.id_format
            if fmt is not None:
                cased = ascii_case(norm, fmt.case)
                accepted: list[tuple[int, int]] = []
                for m in fmt.scanner.finditer(cased):
                    canon = fmt.canonical_of(m)
                    if fmt.space_form(m) and canon not in self.known_ids[t]:
                        unresolved["space_unknown"] += 1
                        continue
                    start, end = idx[m.start()], idx[m.end() - 1] + 1
                    surface = text[start:end]
                    method = "exact" if surface == canon else "variant"
                    candidates.append(Mention(t, canon, start, end, surface, method, self._conf[method]))
                    accepted.append(m.span())
                j = 0
                for m in fmt.shadow.finditer(cased):
                    a, b = m.span()
                    while j < len(accepted) and accepted[j][1] <= a:
                        j += 1
                    if j < len(accepted) and accepted[j][0] < b:
                        continue
                    kind = fmt.non_ascii_kind(m)
                    if kind is not None:
                        unresolved[kind] += 1
            if t in self._alias:
                if phrase is None:
                    phrase = fold_phrase(text)
                regex, table = self._alias[t]
                fp, fidx = phrase
                for m in regex.finditer(fp):
                    start, end = fidx[m.start()], fidx[m.end() - 1] + 1
                    candidates.append(Mention(t, table[m.group()], start, end, text[start:end], "alias",
                                              self._conf["alias"]))
            candidates.sort(key=lambda c: (c.start, -(c.end - c.start)))
            last_end = -1
            for c in candidates:
                if c.start >= last_end:
                    mentions.append(c)
                    last_end = c.end
        mentions.sort(key=lambda m: (m.start, m.end, m.entity_type, m.entity_id))
        return ScanResult(mentions=tuple(mentions), unresolved=MappingProxyType(unresolved))

    def resolve_exact(self, entity_type: str, text: Any) -> Mention | None:
        """The one mention of ``entity_type`` that covers the whole stripped ``text``, else None. A canonical id of
        an alias-only type resolves as itself (scan finds alias-only types only through aliases)."""
        et = self.pack.entity_types.get(entity_type) if isinstance(entity_type, str) else None
        if et is None or not isinstance(text, str):
            return None
        s = text.strip()
        if not s:
            return None
        if et.id_format is None and s in et.ids:
            return Mention(entity_type, s, 0, len(s), s, "exact", self._conf["exact"])
        for m in self.scan(s, types=(entity_type,)).mentions:
            if m.start == 0 and m.end == len(s):
                return m
        return None

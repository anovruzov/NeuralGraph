"""Pure text helpers for connectors and the pipeline (docs/mycelic/INGESTION.md §4.4, §4.5, §4.7, §10.6).

* :func:`canonical_body` is the normalization the content hash is computed over.
* :func:`split_body` separates a body into the author's own text, quoted replies, forwarded blocks and a signature, so the
  record's root reflects what its author wrote (a quoted or forwarded statement is never attributed to the replier).
* :func:`detect` flags secrets and prompt-injection markers. Flags only: content is data and never changes behaviour.
* :func:`mask_secrets` replaces detected credentials before anything is stored (the redact stage).

Everything here is deterministic and does no I/O.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

MIN_OWN_CHARS = 24          # below this many non-space characters of own text, a forward is a pure copy (§4.5 rule 2)
MIN_QUOTE_CHARS = 64        # quoted segments shorter than this are dropped rather than becoming child records

_FORWARD_START = re.compile(r"^\s*(-{5,}\s*forwarded message\s*-{5,}|begin forwarded message:)\s*$", re.IGNORECASE)
_ORIGINAL_START = re.compile(r"^\s*-{3,}\s*original message\s*-{3,}\s*$", re.IGNORECASE)
_WROTE_LINE = re.compile(r"^\s*on\b.{0,200}\bwrote:\s*$", re.IGNORECASE)
_HEADER_LINE = re.compile(r"^\s*(from|date|sent|subject|to|cc|reply-to)\s*:", re.IGNORECASE)
_SIGNATURE = re.compile(r"^-- ?$")

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("chat_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    ("url_credentials", re.compile(r"(?<=://)[^/\s:@]{1,64}:[^/\s@]{1,128}(?=@)")),
)

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier)\b.{0,20}\b(instructions?|prompts?|rules?)\b", re.IGNORECASE),
    re.compile(r"\bsystem prompt\b", re.IGNORECASE),
    re.compile(r"\byou are now\b", re.IGNORECASE),
    re.compile(r"<\|[^|>]{1,40}\|>"),
    re.compile(r"\"(tool_calls|function_call)\"\s*:", re.IGNORECASE),
    re.compile(r"<\s*(script|iframe)\b", re.IGNORECASE),
)

FLAG_SECRET = "contains_secret"
FLAG_SUSPICIOUS = "suspicious_instructions"


@dataclass(frozen=True)
class Segment:
    text: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SplitBody:
    own_text: str
    quoted: tuple[Segment, ...]
    forwarded: tuple[Segment, ...]
    signature: str


def canonical_body(text: str | None) -> str:
    """NFC, ``\\n`` line endings, trailing spaces removed from every line, blank lines removed at both ends; case kept."""
    s = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "section", "article"}
    _DROP = {"script", "style", "head", "title", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self._DROP:
            self._drop += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP:
            self._drop = max(0, self._drop - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._drop:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """HTML to plain text with scripts, styles and hidden heads dropped (never executed, never fetched)."""
    p = _TextExtractor()
    try:
        p.feed(markup or "")
        p.close()
    except Exception:   # malformed markup: fall back to tag stripping
        return canonical_body(html.unescape(re.sub(r"<[^>]+>", " ", markup or "")))
    text = "".join(p.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return canonical_body(text)


def to_plain(text: str | None, content_type: str = "text/plain") -> str:
    if (content_type or "").lower().startswith("text/html"):
        return html_to_text(text or "")
    return canonical_body(text)


def split_body(raw_text: str | None, content_type: str = "text/plain") -> SplitBody:
    """Separate own text from quoted replies (``>`` lines, "On ... wrote:" blocks, ``-----Original Message-----``),
    forwarded blocks (``---------- Forwarded message ---------``, ``Begin forwarded message:`` and the header block that
    follows) and a ``-- `` signature."""
    lines = to_plain(raw_text, content_type).split("\n")
    own: list[str] = []
    quoted: list[Segment] = []
    forwarded: list[Segment] = []
    signature: list[str] = []
    i, n = 0, len(lines)
    quote_buf: list[str] = []
    quote_start = 0

    def flush_quote(end: int) -> None:
        nonlocal quote_buf
        if quote_buf:
            text = canonical_body("\n".join(quote_buf))
            if text:
                quoted.append(Segment(text, quote_start, end))
            quote_buf = []

    while i < n:
        line = lines[i]
        if _SIGNATURE.match(line):
            flush_quote(i)
            signature = lines[i + 1:]
            break
        if _FORWARD_START.match(line):
            flush_quote(i)
            j = i + 1
            while j < n and (_HEADER_LINE.match(lines[j]) or (not lines[j].strip() and j == i + 1)):
                j += 1
            # the forwarded block runs to the end (a nested forward stays inside it)
            text = canonical_body("\n".join(lines[j:]))
            if text:
                forwarded.append(Segment(text, j, n))
            break
        if _ORIGINAL_START.match(line):
            flush_quote(i)
            j = i + 1
            while j < n and _HEADER_LINE.match(lines[j]):
                j += 1
            text = canonical_body("\n".join(lines[j:]))
            if text:
                quoted.append(Segment(text, j, n))
            break
        if _WROTE_LINE.match(line) and i + 1 < n and lines[i + 1].lstrip().startswith(">"):
            flush_quote(i)
            quote_start = i + 1
            i += 1
            continue
        if line.lstrip().startswith(">"):
            if not quote_buf:
                quote_start = i
            quote_buf.append(re.sub(r"^\s*>+ ?", "", line))
            i += 1
            continue
        flush_quote(i)
        own.append(line)
        i += 1
    flush_quote(n)
    return SplitBody(own_text=canonical_body("\n".join(own)), quoted=tuple(quoted), forwarded=tuple(forwarded),
                     signature=canonical_body("\n".join(signature)))


def non_space_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def detect(*texts: str) -> set[str]:
    """Flags for the record: ``contains_secret`` (credentials, keys, tokens) and ``suspicious_instructions``."""
    flags: set[str] = set()
    for t in texts:
        if not t:
            continue
        if any(p.search(t) for _name, p in SECRET_PATTERNS):
            flags.add(FLAG_SECRET)
        if any(p.search(t) for p in INJECTION_PATTERNS):
            flags.add(FLAG_SUSPICIOUS)
    return flags


def mask_secrets(text: str) -> tuple[str, int]:
    """Replace every detected credential with ``[redacted:<kind>]``. Returns the text and the number of replacements."""
    total = 0
    out = text or ""
    for name, p in SECRET_PATTERNS:
        out, k = p.subn(f"[redacted:{name}]", out)
        total += k
    return out, total

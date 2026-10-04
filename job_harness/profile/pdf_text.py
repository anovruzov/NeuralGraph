"""Minimal, dependency-free PDF text extraction.

The last fallback behind pdftotext, pypdf and pdfminer. It handles the kind of
PDF a résumé actually is: text drawn with Tj/TJ operators in Flate-compressed
content streams. It is not a general PDF engine -- scanned images and exotic
CID encodings will come back empty, which the caller treats as "no text".
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
# Text-showing operators: (str) Tj | (str) ' | (str) " | [ ... ] TJ
_SHOW = re.compile(rb"(\((?:[^()\\]|\\.|\([^)]*\))*\)|\[[^\]]*\])\s*(TJ|Tj|'|\")")
_ARRAY_ITEM = re.compile(rb"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>|-?[\d.]+")
_TJ_GAP = re.compile(rb"^-?[\d.]+$")

_ESCAPES = {
    b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
    b"(": b"(", b")": b")", b"\\": b"\\",
}


def _unescape(raw: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(raw):
        ch = raw[i:i + 1]
        if ch != b"\\":
            out += ch
            i += 1
            continue
        nxt = raw[i + 1:i + 2]
        if nxt in _ESCAPES:
            out += _ESCAPES[nxt]
            i += 2
        elif nxt.isdigit():                      # \ooo octal
            digits = raw[i + 1:i + 4]
            octal = bytes(c for c in digits if 0x30 <= c <= 0x37)
            try:
                out += bytes([int(octal, 8) & 0xFF])
            except ValueError:
                pass
            i += 1 + len(octal)
        elif nxt in (b"\n", b"\r"):               # line continuation
            i += 2
        else:
            out += nxt
            i += 2
    return bytes(out)


def _decode(raw: bytes) -> str:
    text = _unescape(raw)
    if text.startswith(b"\xfe\xff"):              # UTF-16BE with BOM
        return text.decode("utf-16-be", errors="ignore")
    return text.decode("latin-1", errors="ignore")


def _hex_string(token: bytes) -> str:
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", token)
    if len(digits) % 2:
        digits += b"0"
    try:
        data = bytes.fromhex(digits.decode("ascii"))
    except ValueError:
        return ""
    if len(data) >= 2 and data[0] == 0 and any(data[1::2]):
        # Looks like 2-byte codes; keep the low byte of each pair.
        return data[1::2].decode("latin-1", errors="ignore")
    return data.decode("latin-1", errors="ignore")


def _text_from_stream(content: bytes) -> str:
    pieces: list[str] = []
    for match in _SHOW.finditer(content):
        token, operator = match.group(1), match.group(2)
        if token.startswith(b"("):
            pieces.append(_decode(token[1:-1]))
        else:                                     # TJ array
            for item in _ARRAY_ITEM.finditer(token):
                value = item.group(0)
                if value.startswith(b"("):
                    pieces.append(_decode(value[1:-1]))
                elif value.startswith(b"<"):
                    pieces.append(_hex_string(value))
                elif _TJ_GAP.match(value):
                    # A large negative kern is a word gap in many producers.
                    try:
                        if float(value) < -150:
                            pieces.append(" ")
                    except ValueError:
                        pass
        if operator in (b"'", b'"'):
            pieces.append("\n")
    return "".join(pieces)


def extract(path: str | Path) -> str:
    """Return the text of a PDF, or "" if it cannot be read as text."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""

    chunks: list[str] = []
    for match in _STREAM.finditer(data):
        raw = match.group(1)
        for candidate in (raw, raw.strip(b"\r\n")):
            try:
                content = zlib.decompress(candidate)
                break
            except zlib.error:
                content = b""
        if not content:
            # Uncompressed content streams exist too.
            content = raw if b"Tj" in raw or b"TJ" in raw else b""
        if not content:
            continue
        text = _text_from_stream(content)
        if text.strip():
            chunks.append(text)

    joined = "\n".join(chunks)
    joined = re.sub(r"[ \t]+", " ", joined)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()

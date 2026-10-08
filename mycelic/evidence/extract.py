"""Text from uploaded documents: plain text and Markdown, JSON, CSV, DOCX and PDF.

Uploads are untrusted input, so every format has a bound: the raw size is capped by the API, a DOCX is a zip whose
decompressed XML is read with a hard limit (no zip bombs; the standard library's expat refuses entity expansion
attacks and resolves no external entities), PDFs are read page by page up to a page cap with ``pypdf``, CSV and JSON
are flattened into readable lines up to a row cap. Nothing in a file changes what the code does: the result is text
for the holder's normal ingestion, which redacts secrets and applies the owner's deny patterns.

Structured files keep their structure in the text (``column: value`` per CSV row, ``path: value`` per JSON leaf), so
retrieval and the cross-app linker see names next to values.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any
from xml.etree import ElementTree

TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".log", ".rst")
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS + (".json", ".jsonl", ".csv", ".docx", ".pdf")
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_ROWS = 20_000
MAX_JSON_LEAVES = 50_000
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractError(ValueError):
    """The file cannot be read as its type (or exceeds a bound). The message names the problem, never the content."""


def extract_text(filename: str, raw: bytes) -> tuple[str, dict[str, Any]]:
    """``(text, info)`` where ``info`` names the format and counts (pages, rows) for the document's metadata."""
    lower = (filename or "").lower()
    if lower.endswith(TEXT_EXTENSIONS):
        return raw.decode("utf-8", errors="replace"), {"format": "text"}
    if lower.endswith(".csv"):
        return _csv(raw)
    if lower.endswith(".jsonl"):
        return _jsonl(raw)
    if lower.endswith(".json"):
        return _json(raw)
    if lower.endswith(".docx"):
        return _docx(raw)
    if lower.endswith(".pdf"):
        return _pdf(raw)
    raise ExtractError(f"unsupported file type; use one of {', '.join(SUPPORTED_EXTENSIONS)}")


def _csv(raw: bytes) -> tuple[str, dict[str, Any]]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, None)
    if not header:
        return "", {"format": "csv", "rows": 0}
    header = [h.strip() or f"column {i + 1}" for i, h in enumerate(header)]
    lines, rows = [], 0
    for row in reader:
        if rows >= MAX_ROWS:
            break
        cells = [f"{h}: {v.strip()}" for h, v in zip(header, row) if v and v.strip()]
        if cells:
            lines.append("; ".join(cells))
            rows += 1
    return "\n".join(lines), {"format": "csv", "rows": rows, "columns": len(header), "truncated": rows >= MAX_ROWS}


def _flatten(value: Any, path: str, out: list[str]) -> None:
    if len(out) >= MAX_JSON_LEAVES:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(v, f"{path}[{i}]", out)
    elif value is not None and value != "":
        out.append(f"{path}: {value}" if path else str(value))


def _json(raw: bytes) -> tuple[str, dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except ValueError:
        raise ExtractError("the file is not valid JSON") from None
    out: list[str] = []
    _flatten(data, "", out)
    return "\n".join(out), {"format": "json", "leaves": len(out), "truncated": len(out) >= MAX_JSON_LEAVES}


def _jsonl(raw: bytes) -> tuple[str, dict[str, Any]]:
    out: list[str] = []
    rows = 0
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            raise ExtractError(f"line {rows + 1} is not valid JSON") from None
        leaves: list[str] = []
        _flatten(item, "", leaves)
        out.append("; ".join(leaves))
        rows += 1
        if rows >= MAX_ROWS:
            break
    return "\n".join(out), {"format": "jsonl", "rows": rows, "truncated": rows >= MAX_ROWS}


def _docx(raw: bytes) -> tuple[str, dict[str, Any]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        info = zf.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ExtractError("the file is not a DOCX document") from None
    if info.file_size > MAX_XML_BYTES:
        raise ExtractError("the document is too large to read")
    with zf.open(info) as f:
        xml = f.read(MAX_XML_BYTES + 1)
    if len(xml) > MAX_XML_BYTES:
        raise ExtractError("the document is too large to read")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        raise ExtractError("the document's XML is damaged") from None
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs), {"format": "docx", "paragraphs": len(paragraphs)}


def _pdf(raw: bytes) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise ExtractError("PDF needs the pypdf package on this server") from None
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ExtractError("the PDF is encrypted")
        pages = []
        for i, page in enumerate(reader.pages):
            if i >= MAX_PDF_PAGES:
                break
            pages.append(page.extract_text() or "")
    except ExtractError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, IndexError):
        raise ExtractError("the PDF cannot be read") from None
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text:
        raise ExtractError("the PDF has no extractable text (a scan needs OCR, which is not supported)")
    return text, {"format": "pdf", "pages": len(pages), "truncated": len(reader.pages) > MAX_PDF_PAGES}

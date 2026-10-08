"""Document uploads: text from DOCX, PDF, CSV, JSON and JSONL, with the bounds that keep hostile files harmless."""
from __future__ import annotations

import io
import zipfile

import pytest

from mycelic.evidence.extract import MAX_XML_BYTES, ExtractError, extract_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    """A minimal, valid PDF 1.4 with one page of Helvetica text (cross-reference offsets computed)."""
    stream = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({ln}) Tj 0 -16 Td" for ln in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_docx_paragraphs():
    text, info = extract_text("retro.docx", make_docx(["Deploy approvals take two days.", "The change board meets twice a week."]))
    assert text.splitlines() == ["Deploy approvals take two days.", "The change board meets twice a week."] and info["format"] == "docx"


def test_docx_bomb_and_garbage_are_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<a>" + "x" * (MAX_XML_BYTES + 10) + "</a>")
    with pytest.raises(ExtractError):
        extract_text("bomb.docx", buf.getvalue())
    with pytest.raises(ExtractError):
        extract_text("fake.docx", b"not a zip")


def test_pdf_text():
    text, info = extract_text("roster.pdf", make_pdf(["On-call rotation has 6 engineers.", "Pages take 20 minutes to acknowledge."]))
    assert "6 engineers" in text and "20 minutes" in text and info == {"format": "pdf", "pages": 1, "truncated": False}
    with pytest.raises(ExtractError):
        extract_text("broken.pdf", b"%PDF-1.4 garbage")


def test_pdf_without_text_says_why():
    with pytest.raises(ExtractError, match="no extractable text"):
        extract_text("scan.pdf", make_pdf([]))


def test_csv_json_jsonl_keep_names_next_to_values():
    text, info = extract_text("tickets.csv", b"ticket,team,hours\nT-1,Support,52\nT-2,Dispatch,\n")
    assert text.splitlines() == ["ticket: T-1; team: Support; hours: 52", "ticket: T-2; team: Dispatch"] and info["rows"] == 2
    text, _ = extract_text("svc.json", b'{"service": {"name": "checkout", "deps": ["httpclient 4.2"]}}')
    assert text.splitlines() == ["service.name: checkout", "service.deps[0]: httpclient 4.2"]
    text, info = extract_text("log.jsonl", b'{"a": 1}\n\n{"b": {"c": "x"}}\n')
    assert text.splitlines() == ["a: 1", "b.c: x"] and info["rows"] == 2
    with pytest.raises(ExtractError):
        extract_text("bad.json", b"{nope")
    with pytest.raises(ExtractError):
        extract_text("x.exe", b"MZ")

"""Render the markdown reports to PDF (reportlab; no LaTeX / pandoc required).

Supports the subset of markdown these reports use: headings, paragraphs with
inline bold / italic / code, pipe tables, fenced code blocks, blockquotes,
bullet lists, images and horizontal rules.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, Image, ListFlowable,
                                ListItem, PageBreak, PageTemplate, Paragraph, Preformatted,
                                Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
AVAIL = PAGE_W - 2 * MARGIN

SS = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=SS["BodyText"], fontSize=9, leading=12.5,
                      spaceAfter=5, alignment=TA_LEFT)
H1 = ParagraphStyle("h1", parent=SS["Heading1"], fontSize=17, leading=21, spaceBefore=6,
                    spaceAfter=8, textColor=colors.HexColor("#10243e"))
H2 = ParagraphStyle("h2", parent=SS["Heading2"], fontSize=13, leading=17, spaceBefore=13,
                    spaceAfter=5, textColor=colors.HexColor("#10243e"))
H3 = ParagraphStyle("h3", parent=SS["Heading3"], fontSize=10.5, leading=14, spaceBefore=9,
                    spaceAfter=4, textColor=colors.HexColor("#22405f"))
CODE = ParagraphStyle("code", parent=SS["Code"], fontSize=7.6, leading=9.6,
                      backColor=colors.HexColor("#f4f5f7"), borderPadding=4,
                      leftIndent=4, spaceAfter=6)
QUOTE = ParagraphStyle("quote", parent=BODY, leftIndent=10, textColor=colors.HexColor("#41506b"),
                       borderPadding=2)
CAP = ParagraphStyle("cap", parent=BODY, fontSize=8, textColor=colors.HexColor("#5a6472"),
                     spaceBefore=2, spaceAfter=9)
TH = ParagraphStyle("th", parent=BODY, fontSize=6.6, leading=8.2, spaceAfter=0,
                    textColor=colors.white)
TD = ParagraphStyle("td", parent=BODY, fontSize=6.6, leading=8.2, spaceAfter=0)


def inline(text: str) -> str:
    """Convert inline markdown to reportlab mini-HTML."""
    text = html.escape(text, quote=False)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<font color="#1a5fb4">\1</font>', text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = text.replace("\\*", "*")
    return text


def make_table(block: list[str]) -> Table:
    rows = []
    for line in block:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) >= 2 and set(rows[1][0].replace(" ", "")) <= set("-:"):
        header, body = rows[0], rows[2:]
    else:
        header, body = rows[0], rows[1:]
    ncol = max(len(r) for r in rows)
    header = header + [""] * (ncol - len(header))
    body = [r + [""] * (ncol - len(r)) for r in body]

    widths = []
    for j in range(ncol):
        longest = max([len(header[j])] + [len(r[j]) for r in body] or [1])
        widths.append(max(3.0, min(longest, 34)))
    total = sum(widths)
    col_w = [AVAIL * w / total for w in widths]

    data = [[Paragraph(inline(c), TH) for c in header]]
    data += [[Paragraph(inline(c), TD) for c in r] for r in body]
    t = Table(data, colWidths=col_w, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def md_to_flowables(md: str, base_dir: Path) -> list:
    flow = []
    lines = md.splitlines()
    i = 0
    bullets: list[str] = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable(
                [ListItem(Paragraph(inline(b), BODY), leftIndent=12) for b in bullets],
                bulletType="bullet", start="•", leftIndent=12, bulletFontSize=7))
            flow.append(Spacer(1, 3))
            bullets = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_bullets()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            flow.append(Preformatted("\n".join(buf), CODE))
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            flush_bullets()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            flow.append(Spacer(1, 3))
            flow.append(make_table(block))
            flow.append(Spacer(1, 7))
            continue

        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if m:
            flush_bullets()
            path = (base_dir / m.group(2)).resolve()
            if path.exists():
                from reportlab.lib.utils import ImageReader
                iw, ih = ImageReader(str(path)).getSize()
                w = AVAIL
                h = w * ih / iw
                if h > PAGE_H - 2 * MARGIN - 40:
                    h = PAGE_H - 2 * MARGIN - 40
                    w = h * iw / ih
                flow.append(Spacer(1, 4))
                flow.append(Image(str(path), width=w, height=h))
                if m.group(1):
                    flow.append(Paragraph(inline(m.group(1)), CAP))
                flow.append(Spacer(1, 6))
            i += 1
            continue

        if stripped.startswith("### "):
            flush_bullets(); flow.append(Paragraph(inline(stripped[4:]), H3)); i += 1; continue
        if stripped.startswith("## "):
            flush_bullets(); flow.append(Paragraph(inline(stripped[3:]), H2)); i += 1; continue
        if stripped.startswith("# "):
            flush_bullets(); flow.append(Paragraph(inline(stripped[2:]), H1)); i += 1; continue
        if stripped.startswith("> "):
            flush_bullets(); flow.append(Paragraph(inline(stripped[2:]), QUOTE)); i += 1; continue
        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            flush_bullets()
            flow.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c9ced6"),
                                   spaceBefore=6, spaceAfter=6))
            i += 1
            continue
        if re.match(r"^[-*] ", stripped):
            bullets.append(stripped[2:]); i += 1; continue
        m = re.match(r"^(\d+)\. (.*)", stripped)
        if m:
            bullets.append(m.group(2)); i += 1; continue
        if not stripped:
            flush_bullets(); i += 1; continue

        flush_bullets()
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(\||#|>|```|!\[|[-*] |\d+\. |-{3,})", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        flow.append(Paragraph(inline(" ".join(para)), BODY))
    flush_bullets()
    return flow


def render(md_paths: list[Path], out: Path, title: str):
    doc = BaseDocTemplate(str(out), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN, title=title,
                          author="NeuralGraph large-scale agentic web stress test")
    frame = Frame(MARGIN, MARGIN, AVAIL, PAGE_H - 2 * MARGIN, id="f")

    def on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#7a828d"))
        canvas.drawString(MARGIN, MARGIN - 8, title)
        canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 8, f"page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])
    flow = []
    for k, p in enumerate(md_paths):
        if k:
            flow.append(PageBreak())
        flow.extend(md_to_flowables(p.read_text(), p.parent))
    doc.build(flow)
    return out


def main():
    paths = [p for p in (ROOT / "RESULTS.md", ROOT / "PAPER_SECTION.md") if p.exists()]
    if not paths:
        raise SystemExit("nothing to render: run src/report.py first")
    out = render(paths, ROOT / "RESULTS.pdf", "Large-Scale Agentic Web Stress Test")
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()

"""Append the stress-test section to the paper PDF as new pages.

The paper is only available as a compiled PDF here, so the new section is
rendered to matching US-Letter pages and concatenated; the original pages are
copied through byte-for-byte and are not modified.

    python3 -m experiments.large_scale_agentic_web.src.append_to_paper \
        --paper /path/to/MycelialFabric.pdf [--out out.pdf] [--section-number 9]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, Image, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

from . import make_pdf as MP

ROOT = Path(__file__).resolve().parents[1]

PAGE_W, PAGE_H = LETTER
LMARGIN = 1.25 * inch
RMARGIN = 1.25 * inch
TMARGIN = 1.0 * inch
BMARGIN = 1.0 * inch
AVAIL = PAGE_W - LMARGIN - RMARGIN

SS = getSampleStyleSheet()
PBODY = ParagraphStyle("pbody", parent=SS["BodyText"], fontName="Times-Roman", fontSize=9.6,
                       leading=12.2, spaceAfter=6, alignment=TA_JUSTIFY, firstLineIndent=0)
PH1 = ParagraphStyle("ph1", parent=SS["Heading1"], fontName="Times-Bold", fontSize=13,
                     leading=16, spaceBefore=10, spaceAfter=7, textColor=colors.black)
PH2 = ParagraphStyle("ph2", parent=SS["Heading2"], fontName="Times-Bold", fontSize=11,
                     leading=14, spaceBefore=10, spaceAfter=5, textColor=colors.black)
PH3 = ParagraphStyle("ph3", parent=SS["Heading3"], fontName="Times-Italic", fontSize=10,
                     leading=13, spaceBefore=8, spaceAfter=4, textColor=colors.black)
PCAP = ParagraphStyle("pcap", parent=PBODY, fontSize=8.4, leading=10.6, spaceBefore=4,
                      spaceAfter=10, alignment=TA_LEFT)
PTH = ParagraphStyle("pth", parent=PBODY, fontName="Times-Bold", fontSize=6.6, leading=8.2,
                     spaceAfter=0, alignment=TA_LEFT)
PTD = ParagraphStyle("ptd", parent=PBODY, fontName="Times-Roman", fontSize=6.6, leading=8.2,
                     spaceAfter=0, alignment=TA_LEFT)


def paper_table(block: list[str]) -> Table:
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in block]
    if len(rows) >= 2 and set(rows[1][0].replace(" ", "")) <= set("-:"):
        header, body = rows[0], rows[2:]
    else:
        header, body = rows[0], rows[1:]
    ncol = max(len(r) for r in rows)
    header = header + [""] * (ncol - len(header))
    body = [r + [""] * (ncol - len(r)) for r in body]
    widths = [max(3.0, min(max([len(header[j])] + [len(r[j]) for r in body]), 30))
              for j in range(ncol)]
    total = sum(widths)
    data = [[Paragraph(MP.inline(c), PTH) for c in header]]
    data += [[Paragraph(MP.inline(c), PTD) for c in r] for r in body]
    t = Table(data, colWidths=[AVAIL * w / total for w in widths], repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.black),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 0.7, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
    ]))
    return t


def section_flowables(md: str, section_number: int, base_dir: Path) -> list:
    flow, i = [], 0
    lines = md.splitlines()
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i]); i += 1
            flow.append(Spacer(1, 3)); flow.append(paper_table(block)); flow.append(Spacer(1, 8))
            continue
        if line.startswith("### "):
            flow.append(Paragraph(MP.inline(line[4:]), PH3)); i += 1; continue
        if line.startswith("## "):
            title = line[3:]
            flow.append(Paragraph(f"{section_number} &nbsp;{MP.inline(title)}", PH1)); i += 1; continue
        if line.startswith("# "):
            flow.append(Paragraph(MP.inline(line[2:]), PH1)); i += 1; continue
        if line in ("---", "***") or not line:
            i += 1; continue
        para = [line]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(\||#|---)", lines[i].strip()):
            para.append(lines[i].strip()); i += 1
        text = " ".join(para)
        style = PCAP if text.startswith("**Table") or text.startswith("**Figure") else PBODY
        flow.append(Paragraph(MP.inline(text), style))
    return flow


def figure_pages(fig_dir: Path) -> list:
    """Figures 1 and 2 on their own page, at paper width."""
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import PageBreak
    flow = [PageBreak()]
    for name, cap in (("fig1_money_survival_vs_failure.png",
                       "Figure 1: knowledge survival and task accuracy as failure escalates."),
                      ("fig2_money_frontier.png",
                       "Figure 2: resilience-efficiency frontier under the white-box attack.")):
        p = fig_dir / name
        if not p.exists():
            continue
        iw, ih = ImageReader(str(p)).getSize()
        w = AVAIL
        h = w * ih / iw
        cap_h = 30
        if h > PAGE_H - TMARGIN - BMARGIN - cap_h:
            h = PAGE_H - TMARGIN - BMARGIN - cap_h
            w = h * iw / ih
        flow.append(Image(str(p), width=w, height=h))
        flow.append(Paragraph(cap, PCAP))
        flow.append(Spacer(1, 10))
    return flow


def render_section(md_path: Path, out_pdf: Path, section_number: int, start_page: int):
    doc = BaseDocTemplate(str(out_pdf), pagesize=LETTER, leftMargin=LMARGIN, rightMargin=RMARGIN,
                          topMargin=TMARGIN, bottomMargin=BMARGIN,
                          title="Large-Scale Agentic Web Stress Test")
    frame = Frame(LMARGIN, BMARGIN, AVAIL, PAGE_H - TMARGIN - BMARGIN, id="f")

    def on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 9)
        canvas.drawCentredString(PAGE_W / 2, BMARGIN - 22,
                                 str(start_page + canvas.getPageNumber() - 1))
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])
    flow = section_flowables(md_path.read_text(), section_number, md_path.parent)
    flow.extend(figure_pages(ROOT / "figures"))
    doc.build(flow)
    return out_pdf


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", required=True, help="the compiled paper PDF to append to")
    ap.add_argument("--out", default=None)
    ap.add_argument("--section-number", type=int, default=9)
    ap.add_argument("--md", default=str(ROOT / "PAPER_SECTION.md"))
    args = ap.parse_args(argv)

    from pypdf import PdfReader, PdfWriter
    paper = Path(args.paper)
    reader = PdfReader(str(paper))
    n_pages = len(reader.pages)

    tmp = ROOT / "figures" / "_new_section.pdf"
    render_section(Path(args.md), tmp, args.section_number, n_pages + 1)

    out = Path(args.out) if args.out else ROOT / (paper.stem + "_with_stress_test.pdf")
    w = PdfWriter()
    for p in reader.pages:
        w.add_page(p)
    for p in PdfReader(str(tmp)).pages:
        w.add_page(p)
    with open(out, "wb") as fh:
        w.write(fh)
    tmp.unlink(missing_ok=True)
    total = len(PdfReader(str(out)).pages)
    print(f"wrote {out}: {n_pages} original pages + {total - n_pages} new "
          f"(section {args.section_number}) = {total} pages")
    return out


if __name__ == "__main__":
    main()

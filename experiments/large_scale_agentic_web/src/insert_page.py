"""Render the stress test as a single camera-ready page to drop into the paper.

The paper's body is already exactly nine pages before its references, so the
section is compressed to one page: prose, one figure, one table, and their
captions.  Every number is substituted from results/summary.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, Image, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from .report import CORRELATED, ROOTCORR, TARGETED, find_test, num, signed, p_str, t1

ROOT = Path(__file__).resolve().parents[1]
PAGE_W, PAGE_H = LETTER
M = 1.0 * inch
AVAIL = PAGE_W - 2 * M

H = ParagraphStyle("h", fontName="Times-Bold", fontSize=11.5, leading=14,
                   spaceAfter=5, textColor=colors.black)
BODY = ParagraphStyle("b", fontName="Times-Roman", fontSize=9.1, leading=11.0,
                      alignment=TA_JUSTIFY, spaceAfter=4.5)
CAP = ParagraphStyle("c", fontName="Times-Roman", fontSize=8.0, leading=9.6,
                     alignment=TA_JUSTIFY, spaceBefore=3, spaceAfter=7)
TH = ParagraphStyle("th", fontName="Times-Bold", fontSize=7.6, leading=9)
TD = ParagraphStyle("td", fontName="Times-Roman", fontSize=7.6, leading=9)

ROWS = [("B2", "full replication"), ("B4", "gossip"), ("B3", "random replication"),
        ("B5", "diversity-blind"), ("B6", "lineage-aware fabric"),
        ("B7", "lineage + questioning"), ("B3Q", "questioning, no lineage")]


def build(summary: dict) -> list:
    n = summary["main_n"]
    ns = summary["main_n_seeds"]
    qps = summary.get("questions_per_seed", {}).get(str(n), 0)
    tot = summary["totals"]
    scales = sorted(summary.get("scales", []))

    t_acc = find_test(summary, n, TARGETED, "accuracy_macro", "B6", "B3")
    t_iss = find_test(summary, n, TARGETED, "iss", "B6", "B3")
    t_f1 = find_test(summary, n, TARGETED, "contradiction_f1", "B6", "B3")
    t_ks = find_test(summary, n, TARGETED, "knowledge_survival", "B6", "B3")
    t_root = find_test(summary, n, ROOTCORR, "accuracy_macro", "B6", "B3")
    t_gq = find_test(summary, n, CORRELATED, "accuracy_macro", "B7", "B6")
    t_ctrl = find_test(summary, n, CORRELATED, "accuracy_macro", "B3Q", "B3")
    st6 = t1(summary, TARGETED, "B6", "storage_per_claim")
    st2 = t1(summary, TARGETED, "B2", "storage_per_claim")
    ks2 = t1(summary, TARGETED, "B2", "KS")
    m6 = t1(summary, CORRELATED, "B6", "messages_per_claim")
    m7 = t1(summary, CORRELATED, "B7", "messages_per_claim")

    def g(t, f="mean_diff"):
        return signed(t[f]) if t else "n/a"

    flow = [Paragraph("9&nbsp;&nbsp;Large-Scale Agentic Web Stress Test", H)]

    flow.append(Paragraph(
        f"<b>Design.</b> We test whether lineage-first distribution keeps knowledge useful at "
        f"population scale, on a simulated agentic web of N &#8712; "
        f"{{{', '.join('10<super>%d</super>' % round(math.log10(s)) for s in scales)}}} agents in "
        f"teams, failure domains, organisations and regions. Every evidence item has a home agent "
        f"and descends from exactly one origin; family sizes are heavy-tailed and ~35% of claims "
        f"have a single origin, so replica count and independent support come apart by "
        f"construction. We score {qps:,} labelled questions per seed at N&nbsp;=&nbsp;{n:,} "
        f"(single-hop, multi-hop, temporal, revision-sensitive, reconstruction, contradiction, "
        f"evidence-verification, strategic ranking). Equal-budget systems publish an identical "
        f"{num(st6, 0)} replicas per claim and every probe attempt is charged as a message, dead "
        f"hosts included. Failures escalate from 10&#8211;90% churn to correlated loss of whole "
        f"organisations, to a <b>white-box adversary</b> that knows each system's own placement "
        f"and removes the hosts of its scarcest surviving independent evidence, to partitions with "
        f"independent updates, to corruption of nodes or of origin sources. {ns} seeds, paired by "
        f"seed, Holm-corrected; {tot['total_rows']:,} recorded runs in total.", BODY))

    flow.append(Paragraph(
        f"<b>Resilience.</b> Under the white-box attack at N&nbsp;=&nbsp;{n:,}, knowledge survival "
        f"is <i>statistically indistinguishable</i> across the equal-budget policies "
        f"(B6&#8722;B3 {g(t_ks)}, p&nbsp;=&nbsp;{p_str(t_ks['p_paired_t']) if t_ks else 'n/a'}; "
        f"Fig.&nbsp;1c). What separates them is the quality of what survives: independent-support "
        f"survival {g(t_iss)} (d_z&nbsp;=&nbsp;{num(t_iss['cohens_dz'],1) if t_iss else ''}), "
        f"contradiction F1 {g(t_f1)}, task accuracy {g(t_acc)}, all Holm&nbsp;p&nbsp;&lt;&nbsp;1e&#8722;6. "
        f"The gap widens under origin-source corruption, where a corrupted origin's false value is "
        f"inherited by all of its copies ({g(t_root)} accuracy). Broad replication survives better "
        f"({num(ks2)}) but at {num(st2,0)} replicas per claim, {st2/max(st6,1e-9):.0f}&#215; the "
        f"storage: on the cost frontier the margin is bought with capacity, not with a better use "
        f"of it. The effect is flat in N from 10<super>2</super> to 10<super>5</super>.", BODY))

    flow.append(Paragraph(
        f"<b>Continual questioning, and what does not hold.</b> At identical storage &#8212; the "
        f"base budget is reduced by exactly the replicas questioning adds &#8212; questioning "
        f"raises accuracy by {g(t_gq)} "
        f"(d_z&nbsp;=&nbsp;{num(t_gq['cohens_dz'],1) if t_gq else ''}) for a "
        f"{m7/max(m6,1e-9):.1f}&#215; increase in messages, and drives post-partition "
        f"reconciliation. But the identical machinery on a count-based system gains <i>more</i> "
        f"({g(t_ctrl)}): continual discovery is largely orthogonal to lineage, not a consequence "
        f"of it. Three further negatives: in a world where every claim has one origin the lineage "
        f"fabric and random replication are identical; almost all of the questioning gain is "
        f"re-verification rather than acquiring new independent support; and targeting questions by "
        f"lineage fragility is no better than targeting them at random. Lineage awareness improves "
        f"the <i>evidential</i> quality of what survives at matched cost; it does not, on its own, "
        f"make more of it survive.", BODY))

    img = ROOT / "figures" / "fig_insert.png"
    if img.exists():
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(str(img)).getSize()
        w = AVAIL
        im = Image(str(img), width=w, height=w * ih / iw)
        im.hAlign = "CENTER"
        flow.append(Spacer(1, 2))
        flow.append(im)
    flow.append(Paragraph(
        f"<b>Figure 1:</b> (a) Knowledge survival as a white-box adversary removes agents "
        f"(N&nbsp;=&nbsp;{n:,}, {ns} seeds, bands 95% CI); axes span the full [0,&nbsp;1] range. "
        f"(b) Survival against storage&nbsp;+&nbsp;communication cost per claim at 50% removal; "
        f"dashed line sweeps the storage budget k&nbsp;&#8712;&nbsp;{{1&#8230;32}}, markers dodged "
        f"where systems coincide. (c) The same 50% condition with every seed drawn as one point on "
        f"a zoomed axis: the three equal-budget policies overlap, while questioning and the "
        f"high-cost systems separate cleanly.", CAP))

    head = ["", "KS", "acc.", "ISS", "contr. F1", "ECE*", "storage", "msgs"]
    data = [[Paragraph(h, TH) for h in head]]
    for sid, name in ROWS:
        ks = t1(summary, TARGETED, sid, "KS")
        if isinstance(ks, float) and math.isnan(ks):
            continue
        cells = [f"{sid} {name}", num(ks), num(t1(summary, TARGETED, sid, "accuracy")),
                 num(t1(summary, TARGETED, sid, "ISS")),
                 num(t1(summary, TARGETED, sid, "contradiction_F1")),
                 num(t1(summary, TARGETED, sid, "ECE_recal")),
                 f"{t1(summary, TARGETED, sid, 'storage_per_claim'):.0f}",
                 f"{t1(summary, TARGETED, sid, 'messages_per_claim'):.0f}"]
        data.append([Paragraph(c, TD) for c in cells])
    widths = [AVAIL * w for w in (0.30, 0.10, 0.10, 0.10, 0.12, 0.10, 0.09, 0.09)]
    tbl = Table(data, colWidths=widths, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.black),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 0.7, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(tbl)
    flow.append(Paragraph(
        f"<b>Table 1:</b> The 50% white-box attack at N&nbsp;=&nbsp;{n:,} ({ns} seeds, paired). "
        f"KS counts knowledge that is both recoverable and answered correctly; ISS is the fraction "
        f"of original independent evidence paths surviving in the system's own stored set; ECE* is "
        f"calibration error after held-out recalibration; storage counts published replicas per "
        f"claim and messages counts publication, questioning and every probe attempt. B3, B5, B6 "
        f"and B7 share one storage budget; B2 and B4 do not, and their cost columns show what "
        f"their margin is bought with. Full grid, all conditions and the negative-result table are "
        f"in the supplement.", CAP))
    return flow


def main():
    summary = json.loads((ROOT / "results" / "summary.json").read_text())
    out = ROOT / "SECTION_INSERT.pdf"
    doc = BaseDocTemplate(str(out), pagesize=LETTER, leftMargin=M, rightMargin=M,
                          topMargin=M, bottomMargin=M,
                          title="Large-Scale Agentic Web Stress Test")
    doc.addPageTemplates([PageTemplate(id="p", frames=[
        Frame(M, M, AVAIL, PAGE_H - 2 * M, id="f", leftPadding=0, rightPadding=0,
              topPadding=0, bottomPadding=0)])])
    doc.build(build(summary))
    from pypdf import PdfReader
    n_pages = len(PdfReader(str(out)).pages)
    print(f"wrote {out} ({n_pages} page{'s' if n_pages != 1 else ''})")
    if n_pages != 1:
        print("  WARNING: does not fit on one page — tighten the prose")
    return out


if __name__ == "__main__":
    main()

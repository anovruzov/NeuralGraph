"""Render the generated report to a paginated PDF.

The report is a long Markdown document with a lot of wide numeric tables and
nine figures, which is exactly the shape that converts badly by default: the
tables overflow the page and the figures land wherever they fall.  So this
does three specific things rather than a generic Markdown-to-PDF pass:

  * sizes each table's type from its column count, so a 13-column baseline
    table fits the page instead of being clipped,
  * keeps rows, figures and short sections off page boundaries, and repeats
    table headers when a long table does have to break,
  * forces the collapsed contents block open, since a `<details>` element
    prints as just its summary line.

Chromium does the rendering (it is already present for Playwright), driven
through Playwright so the footer can carry page numbers.

    python3 -m research.mycelic.make_pdf
"""
from __future__ import annotations

import asyncio
import html
import os
import re
from datetime import date
from typing import List

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "docs", "MYCELIC_ENTERPRISE.md")
OUT = os.path.join(ROOT, "docs", "MYCELIC_ENTERPRISE.pdf")

TITLE = "Mycelic — hierarchical enterprise intelligence"
SUBTITLE = "Benchmark report and architecture recommendation"

# Column-count thresholds for table type size.  The widest table in the
# report has 13 columns; at 5.6pt that fits A4 portrait with margins.
TABLE_SIZES = [(12, "t-xs"), (9, "t-sm"), (7, "t-md")]

CSS = """
@page {
  size: A4 portrait;
  margin: 17mm 14mm 16mm 14mm;
}
:root {
  --ink: #16181d;
  --muted: #5b6270;
  --rule: #d4d8e0;
  --rule-soft: #e8ebf0;
  --accent: #2f4858;
  --band: #f4f6f9;
}
* { box-sizing: border-box; }
body {
  font-family: "Charter", "Georgia", "Times New Roman", serif;
  font-size: 9.6pt;
  line-height: 1.48;
  color: var(--ink);
  margin: 0;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
p { margin: 0 0 0.62em; orphans: 3; widows: 3; }
em { color: var(--muted); }

h1, h2, h3, h4 {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: var(--accent);
  line-height: 1.22;
  margin: 1.15em 0 0.45em;
  page-break-after: avoid;
  break-after: avoid;
}
/* PART dividers are h1 in the source; each opens a page. */
h1 {
  font-size: 15pt;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  border-bottom: 1.6pt solid var(--accent);
  padding-bottom: 0.3em;
  page-break-before: always;
  break-before: page;
}
h1.doc-title, h1:first-of-type { page-break-before: avoid; break-before: auto; }
h2 { font-size: 12.4pt; border-bottom: 0.6pt solid var(--rule); padding-bottom: 0.2em; }
h3 { font-size: 10.6pt; }
h4 { font-size: 9.8pt; color: var(--muted); }

ul, ol { margin: 0 0 0.62em; padding-left: 1.25em; }
li { margin-bottom: 0.22em; }

code, kbd {
  font-family: "SF Mono", "DejaVu Sans Mono", Menlo, Consolas, monospace;
  font-size: 0.86em;
  background: var(--band);
  padding: 0.05em 0.3em;
  border-radius: 2px;
}
pre {
  background: var(--band);
  border: 0.5pt solid var(--rule);
  border-radius: 3px;
  padding: 7pt 9pt;
  font-size: 7.2pt;
  line-height: 1.34;
  overflow: visible;
  white-space: pre-wrap;
  page-break-inside: avoid;
  break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: inherit; }

table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5em 0 0.9em;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 7.6pt;
  font-variant-numeric: tabular-nums;
}
table.t-md { font-size: 7.0pt; }
table.t-sm { font-size: 6.4pt; }
table.t-xs { font-size: 5.6pt; }
thead { display: table-header-group; }
th, td {
  border-bottom: 0.5pt solid var(--rule-soft);
  padding: 2.6pt 3.4pt;
  vertical-align: top;
  word-break: normal;
  /* break-word, not anywhere: `anywhere` is taken into account when the
     browser computes a column's minimum width, which lets it squeeze a
     numeric column until "10000" wraps to "1000/0". */
  overflow-wrap: break-word;
}
/* Numbers never wrap; markdown right-aligns the numeric columns for us. */
td[style*="right"], th[style*="right"] { white-space: nowrap; }
/* Long header labels may wrap — that is what makes the numbers fit. */
th { overflow-wrap: anywhere; hyphens: auto; }
th[style*="right"] { white-space: normal; }
th {
  background: var(--band);
  border-bottom: 0.9pt solid var(--rule);
  text-align: left;
  font-weight: 600;
  color: var(--accent);
}
tr { page-break-inside: avoid; break-inside: avoid; }
td sub, td sup { color: var(--muted); font-size: 0.82em; }

img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0.7em auto 0.4em;
  page-break-inside: avoid;
  break-inside: avoid;
}

blockquote {
  margin: 0.6em 0;
  padding-left: 0.8em;
  border-left: 2pt solid var(--rule);
  color: var(--muted);
}
hr { border: 0; border-top: 0.6pt solid var(--rule); margin: 1.1em 0; }
a { color: var(--accent); text-decoration: none; }

/* The contents block is a <details>; print it expanded and unboxed. */
details { display: block; }
details > summary { list-style: none; font-weight: 600; color: var(--accent); }
details > summary::-webkit-details-marker { display: none; }
.contents {
  border: 0.6pt solid var(--rule);
  border-radius: 3px;
  background: #fbfcfd;
  padding: 8pt 12pt 4pt;
  margin: 0.9em 0 1.1em;
  column-count: 2;
  column-gap: 16pt;
  font-size: 8.4pt;
}
.contents summary { column-span: all; margin-bottom: 4pt; }
.contents ul { padding-left: 1.0em; margin-bottom: 0.4em; }
.contents li { margin-bottom: 0.1em; }
.contents p { break-inside: avoid; margin-bottom: 0.25em; }

.cover {
  page-break-after: always;
  break-after: page;
  padding-top: 28mm;
}
.cover .t { font-size: 25pt; line-height: 1.16; margin: 0 0 6pt;
            font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
            color: var(--accent); font-weight: 600; }
.cover .s { font-size: 13pt; color: var(--muted); margin: 0 0 26pt;
            font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; }
.cover .rule { border-top: 2.2pt solid var(--accent); width: 62mm;
               margin: 0 0 22pt; }
.cover dl { font-size: 9.4pt; margin: 0; }
.cover dt { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
            font-size: 7.6pt; letter-spacing: 0.08em; text-transform: uppercase;
            color: var(--muted); margin-top: 11pt; }
.cover dd { margin: 1pt 0 0; }
.cover .note { margin-top: 30mm; font-size: 8.6pt; color: var(--muted);
               border-top: 0.6pt solid var(--rule); padding-top: 8pt; }
"""

HEADER = """
<div style="font-family:Helvetica,Arial,sans-serif;font-size:6.5pt;color:#8b919c;
            width:100%;padding:0 14mm;display:flex;justify-content:space-between;">
  <span>Mycelic — hierarchical enterprise intelligence</span>
  <span>Benchmark report</span>
</div>
"""

FOOTER = """
<div style="font-family:Helvetica,Arial,sans-serif;font-size:6.5pt;color:#8b919c;
            width:100%;padding:0 14mm;display:flex;justify-content:space-between;">
  <span>Generated from research/mycelic/artifacts — every figure is a real run</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>
"""


def _size_tables(doc: str) -> str:
    """Give each table a class chosen from its column count.

    A 13-column table at the body table size runs off the page; shrinking
    every table to fit the widest one would make the narrow ones unreadable.
    """
    def repl(m: re.Match) -> str:
        block = m.group(0)
        head = re.search(r"<thead>.*?</thead>", block, re.S)
        if not head:
            return block
        cells = re.findall(r"<th[^>]*>(.*?)</th>", head.group(0), re.S)
        n = len(cells)
        # A table with few but very wordy headers ("cited evidence names the
        # claimed entity") needs the same treatment as a table with many
        # narrow ones, so count each long header as extra columns.
        longest = max((len(re.sub(r"<[^>]+>", "", c)) for c in cells),
                      default=0)
        weight = n + max(0, (longest - 14)) // 9
        for threshold, cls in TABLE_SIZES:
            if weight >= threshold:
                return block.replace("<table>", f'<table class="{cls}">', 1)
        return block

    return re.sub(r"<table>.*?</table>", repl, doc, flags=re.S)


def _absolutise_images(doc: str) -> str:
    """Rewrite the document-relative figure paths to absolute file URLs."""
    docs_dir = os.path.dirname(SRC)

    def repl(m: re.Match) -> str:
        src = m.group(1)
        if src.startswith(("http:", "https:", "file:", "data:")):
            return m.group(0)
        p = os.path.normpath(os.path.join(docs_dir, src))
        return m.group(0).replace(src, "file://" + p)

    return re.sub(r'<img[^>]*src="([^"]+)"', repl, doc)


def _cover(md_text: str) -> str:
    """A title page carrying the facts a reader needs before page one."""
    # Take the scales from the scale-trend table's header, which is the one
    # place every measured enterprise size appears; the per-scale section
    # headings only cover the scales the full head-to-head was run at.
    hdr = re.search(r"\n\| architecture \|((?: [\d,]+ \|)+)\n", md_text)
    if hdr:
        scales = sorted(int(s.replace(",", ""))
                        for s in re.findall(r"[\d,]+", hdr.group(1)))
    else:
        scales = sorted({int(s.replace(",", ""))
                         for s in re.findall(r"#### ([\d,]+) users", md_text)})
    n_tables = md_text.count("\n|---")
    n_figs = len(re.findall(r"!\[", md_text))
    n_sections = len(re.findall(r"^## \d+\.", md_text, re.M))
    scale_txt = ", ".join(f"{s:,}" for s in scales) if scales else "—"
    return f"""
<section class="cover">
  <div class="t">{html.escape(TITLE.split(' — ')[0])}</div>
  <div class="s">{html.escape(SUBTITLE)}</div>
  <div class="rule"></div>
  <dl>
    <dt>Question</dt>
    <dd>How should thousands of private user-level agents abstract, route,
        combine, question, verify and propagate what they know upward, so that
        an enterprise kernel discovers what no individual could?</dd>
    <dt>Enterprise sizes measured</dt><dd>{scale_txt} users</dd>
    <dt>Contents</dt>
    <dd>{n_sections} sections · {n_tables} tables · {n_figs} figures</dd>
    <dt>Generated</dt><dd>{date.today().isoformat()}</dd>
  </dl>
  <div class="note">
    Every table and figure in this document is computed from the raw per-run
    records in <code>research/mycelic/artifacts/</code> at generation time.
    No number is transcribed by hand. Section&nbsp;5 states exactly which
    results were measured on real models and which were simulated.
  </div>
</section>
"""


def build_html() -> str:
    with open(SRC) as fh:
        md_text = fh.read()

    # Two things have to happen before conversion, not after.  A <details>
    # prints as just its summary line, so it is opened here; and Markdown
    # inside a raw HTML block is passed through verbatim unless the block is
    # marked, which is why the contents list would otherwise appear as its
    # own source.
    md_text_html = md_text.replace(
        "<details>", '<details markdown="1" open class="contents">', 1)

    body = markdown.markdown(
        md_text_html,
        extensions=["tables", "fenced_code", "attr_list", "md_in_html"],
    )
    body = _size_tables(body)
    body = _absolutise_images(body)
    # The source's own title block is replaced by the cover page.
    body = re.sub(r"^<h1>.*?</h1>\s*<h2>.*?</h2>", "", body, count=1, flags=re.S)

    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(TITLE)}</title><style>{CSS}</style></head>"
            f"<body>{_cover(md_text)}{body}</body></html>")


# The environment ships a Chromium build for Playwright; the pip-installed
# Playwright may expect a different revision directory, so point it at the
# binary that is actually here rather than downloading another one.
CHROME = "/opt/pw-browsers/chromium/chrome-linux/chrome"
for _cand in ("/opt/pw-browsers/chromium",
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
    if os.path.exists(_cand) and not os.path.isdir(_cand):
        CHROME = _cand
        break


async def _render(html_path: str, pdf_path: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME if os.path.exists(CHROME) else None,
            args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto("file://" + html_path, wait_until="networkidle")
        await page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=HEADER,
            footer_template=FOOTER,
            margin={"top": "17mm", "bottom": "16mm",
                    "left": "14mm", "right": "14mm"},
        )
        await browser.close()


def build() -> str:
    doc = build_html()
    tmp = os.path.join(os.path.dirname(SRC), ".report.tmp.html")
    with open(tmp, "w") as fh:
        fh.write(doc)
    try:
        asyncio.run(_render(tmp, OUT))
    finally:
        os.remove(tmp)
    return OUT


if __name__ == "__main__":
    p = build()
    print("wrote", p, f"{os.path.getsize(p) / 1e6:.2f} MB")

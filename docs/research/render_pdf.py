#!/usr/bin/env python3
"""Render a research markdown document to PDF with headless Chromium.

Matches how RESULTS_ALL.pdf was produced (Chromium print-to-PDF) so the
documents in this directory keep one look. Relative image references are
inlined as data URIs, which keeps the PDF standalone and lets Chromium
render it without filesystem access to the figures.

    python3 docs/research/render_pdf.py docs/research/CONTINUAL_DISCOVERY.md

Requires: markdown (pip), and Chromium at CHROME_BIN or /opt/pw-browsers/chromium.
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

CSS = """
@page { size: A4; margin: 18mm 16mm 16mm 16mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Charter", "Georgia", "Times New Roman", serif;
  font-size: 10.5pt; line-height: 1.5; color: #16181d; margin: 0;
}
h1 { font-size: 20pt; line-height: 1.2; margin: 0 0 4pt; letter-spacing: -0.01em; }
h2 { font-size: 13pt; margin: 20pt 0 6pt; padding-bottom: 3pt;
     border-bottom: 1px solid #d9dce3; break-after: avoid; }
h2 + p, h2 + table, h2 + blockquote { break-before: avoid; }
p { margin: 0 0 8pt; }
strong { color: #000; }
code, pre { font-family: "SFMono-Regular", "Menlo", "Consolas", monospace; }
code { font-size: 9pt; background: #f3f4f7; padding: 0.5pt 3pt; border-radius: 3px; }
pre { background: #f7f8fa; border: 1px solid #e3e6ec; border-radius: 5px;
      padding: 8pt 10pt; font-size: 8.5pt; line-height: 1.45; overflow-x: auto;
      break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit; }
blockquote { margin: 8pt 0; padding: 6pt 12pt; border-left: 3px solid #b9bfcc;
             color: #3a3f4b; background: #f7f8fa; break-inside: avoid; }
blockquote p:last-child { margin-bottom: 0; }
table { border-collapse: collapse; width: 100%; margin: 10pt 0 12pt;
        font-size: 8.8pt; font-variant-numeric: tabular-nums;
        font-family: "Helvetica Neue", Arial, sans-serif; break-inside: avoid; }
th { text-align: left; font-weight: 600; font-size: 8pt; letter-spacing: 0.02em;
     text-transform: uppercase; color: #4a505e; border-bottom: 1.2px solid #333a47;
     padding: 4pt 6pt; }
td { padding: 3.5pt 6pt; border-bottom: 0.6px solid #e3e6ec; }
tbody tr:last-child td { border-bottom: 1px solid #333a47; }
img { max-width: 100%; display: block; margin: 10pt auto 12pt; break-inside: avoid; }
hr { border: 0; border-top: 1px solid #d9dce3; margin: 14pt 0; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin-bottom: 3pt; }
.banner { background: #fff6e6; border: 1px solid #e8c887; border-radius: 5px;
          padding: 8pt 11pt; margin: 10pt 0 14pt; font-size: 9.5pt; }
.banner p { margin: 0; }
"""


def inline_images(html: str, base_dir: Path) -> str:
    """Replace relative <img src> with data URIs so the PDF is standalone."""
    def repl(match: re.Match) -> str:
        src = match.group(1)
        if src.startswith(("data:", "http://", "https://")):
            return match.group(0)
        path = (base_dir / src).resolve()
        if not path.exists():
            print(f"warning: missing image {src}", file=sys.stderr)
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{data}"'

    return re.sub(r'src="([^"]+)"', repl, html)


def find_chromium() -> str:
    for candidate in (os.environ.get("CHROME_BIN"), "/opt/pw-browsers/chromium",
                      "chromium", "chromium-browser", "google-chrome"):
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    raise SystemExit("no Chromium found; set CHROME_BIN")


def build_html(md_path: Path, title: str | None) -> str:
    text = md_path.read_text()
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "attr_list"])
    # The leading bold paragraph is the evidence-class banner.
    body = body.replace("<p><strong>Evidence class:", '<div class="banner"><p><strong>Evidence class:', 1)
    if '<div class="banner">' in body:
        idx = body.index('<div class="banner">')
        end = body.index("</p>", idx) + 4
        body = body[:end] + "</div>" + body[end:]
    heading = re.search(r"^#\s+(.+)$", text, re.M)
    doc_title = title or (heading.group(1) if heading else md_path.stem)
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{doc_title}</title><style>{CSS}</style></head><body>{body}</body></html>")
    return inline_images(html, md_path.parent)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("markdown", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--title", default=None)
    a = ap.parse_args()

    out = a.out or a.markdown.with_suffix(".pdf")
    html = build_html(a.markdown, a.title)

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(html)
        subprocess.run([
            find_chromium(), "--headless", "--disable-gpu", "--no-sandbox",
            "--no-pdf-header-footer", f"--print-to-pdf={out.resolve()}",
            page.resolve().as_uri(),
        ], check=True, capture_output=True)

    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

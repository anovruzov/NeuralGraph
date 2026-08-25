"""Render the manuscript to a self-contained, print-ready HTML page.

No rendering dependency exists on this machine -- pandoc, weasyprint, wkhtmltopdf
and typst are all absent, and installing them needs system libraries. Rather than
leave the paper unrenderable, this converts the subset of Markdown the manuscript
actually uses, inlines the SVG figures so the output is one file, and applies a
print stylesheet so a browser's print-to-PDF produces the submission artifact.

That last step is the one thing here a human must do, and it is recorded as a
blocker rather than quietly skipped.

    python3.11 -m tools.render_paper --out docs/paper/capability_survival.html
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

SOURCE = Path("docs/paper/capability_survival.md")
FIGDIR = Path("docs/paper/figures")

CSS = """
:root { --ink:#151515; --muted:#5d5d5d; --rule:#d5d5d5; --accent:#0b6fa4; }
* { box-sizing:border-box; }
body { max-width:46rem; margin:0 auto; padding:3rem 1.5rem 5rem;
       font:16px/1.65 Georgia,'Times New Roman',serif; color:var(--ink);
       background:#fff; }
h1 { font-size:1.95rem; line-height:1.25; margin:0 0 .4rem; letter-spacing:-.01em; }
h2 { font-size:1.3rem; margin:2.6rem 0 .7rem; padding-bottom:.3rem;
     border-bottom:1px solid var(--rule); }
h3 { font-size:1.08rem; margin:1.8rem 0 .5rem; }
p, li { margin:.65rem 0; }
code { font:0.86em ui-monospace,'SF Mono',Menlo,monospace;
       background:#f4f4f4; padding:.1em .35em; border-radius:3px; }
pre { background:#f7f7f7; padding:.9rem 1rem; border-radius:5px; overflow-x:auto;
      border:1px solid var(--rule); }
pre code { background:none; padding:0; }
blockquote { margin:1rem 0; padding:.6rem 1rem; border-left:3px solid var(--accent);
             background:#f8fbfd; color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:1.1rem 0; font-size:.9rem;
        display:block; overflow-x:auto; }
th, td { border:1px solid var(--rule); padding:.45rem .6rem; text-align:left;
         vertical-align:top; }
th { background:#f4f4f4; font-weight:600; }
hr { border:0; border-top:1px solid var(--rule); margin:2.2rem 0; }
figure { margin:1.6rem 0; text-align:center; }
figure svg { max-width:100%; height:auto; border:1px solid var(--rule); border-radius:4px; }
a { color:var(--accent); }
.meta { color:var(--muted); font-size:.92rem; }
@media print {
  body { max-width:none; padding:0; font-size:10.5pt; }
  h2 { page-break-after:avoid; }
  table, figure, pre { page-break-inside:avoid; }
  a { color:var(--ink); text-decoration:none; }
}
"""


def _inline(text: str) -> str:
    """Inline markup. Escapes first, so no source text can inject markup."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return out


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    if len(cells) < 2:
        return ""
    head, body = cells[0], cells[2:]          # cells[1] is the alignment rule
    out = ["<table><thead><tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render(md: str, figdir: Path) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(html.escape(lines[i]))
                i += 1
            out.append("<pre><code>" + "\n".join(block) + "</code></pre>")
            i += 1
            continue

        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue

        img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if img:
            svg_path = figdir / Path(img.group(2)).name
            if svg_path.exists():
                svg = svg_path.read_text(encoding="utf-8")
                svg = re.sub(r"<\?xml[^>]*\?>", "", svg).strip()
                out.append(f"<figure>{svg}</figure>")
            else:
                out.append(f'<figure><em>missing figure: {html.escape(img.group(2))}</em></figure>')
            i += 1
            continue

        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
            i += 1
            continue

        if line.strip() in ("---", "***", "___"):
            out.append("<hr/>")
            i += 1
            continue

        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(block))}</blockquote>")
            continue

        if re.match(r"^\s*[-*]\s+", line) or re.match(r"^\s*\d+\.\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and (re.match(r"^\s*[-*]\s+", lines[i])
                                      or re.match(r"^\s*\d+\.\s+", lines[i])):
                items.append(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", lines[i]).rstrip())
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(t)}</li>" for t in items) + f"</{tag}>")
            continue

        if not line.strip():
            i += 1
            continue

        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", ">", "```", "!")):
            para.append(lines[i].strip())
            i += 1
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")

    title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), "Manuscript")
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"/>\n"
        f"<title>{html.escape(title)}</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n" + "\n".join(out) + "\n</body></html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--figdir", type=Path, default=FIGDIR)
    ap.add_argument("--out", type=Path, default=Path("docs/paper/capability_survival.html"))
    args = ap.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.source.read_text(encoding="utf-8"), args.figdir),
                        encoding="utf-8")
    print(f"wrote {args.out}")
    print("PDF: open in a browser and print to PDF. No renderer is installed on "
          "this machine (pandoc/weasyprint/wkhtmltopdf/typst all absent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

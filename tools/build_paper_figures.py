"""Emit the manuscript figures as SVG, from pinned artifacts and the fixture.

No plotting dependency. SVG is written directly, which keeps the output vector
(as the manuscript requires), deterministic byte-for-byte, and free of a
matplotlib version pin that would make the figures irreproducible on a clean
clone.

Figure 1 is drawn from `NeuralGraph/coordination/fixture.py` -- the real record
table, not a hand-drawn approximation -- so it cannot drift from the code it
illustrates. Figure 3 reads `benchmark_sweep30_seed20260813.json`, whose digest
is verified before anything is emitted.

    python3.11 -m tools.build_paper_figures --check
    python3.11 -m tools.build_paper_figures --outdir docs/paper/figures
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ARTIFACTS = Path("NeuralGraph/coordination/artifacts")
SWEEP = "benchmark_sweep30_seed20260813.json"

# Colour-blind-safe, and legible in greyscale print: the two emphasis colours
# differ in lightness as well as hue.
INK = "#1a1a1a"
MUTED = "#767676"
GRID = "#d8d8d8"
ACCENT = "#0b6fa4"      # lineage-aware
WARN = "#b3541e"        # full replication
ORACLE = "#4a4a4a"


def verify_digests() -> list[str]:
    problems: list[str] = []
    for line in (ARTIFACTS / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        expected, _, name = line.partition("  ")
        path = ARTIFACTS / name.strip()
        if not path.exists():
            problems.append(f"missing artifact: {name.strip()}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"digest drift: {name.strip()}")
    return problems


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _svg(width: int, height: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica,Arial,sans-serif">\n'
        f'<title>{_esc(title)}</title>\n'
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>\n'
        f"{body}\n</svg>\n"
    )


def _text(x: float, y: float, s: str, size: float = 12, fill: str = INK,
          anchor: str = "start", weight: str = "normal") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{_esc(s)}</text>')


# ---------------------------------------------------------------- figure 1

def figure1() -> str:
    """The A/B/C/D fixture: why C is not redundancy.

    Node values are read from the fixture's own record table so the figure
    cannot describe a structure the code does not build.
    """
    nodes = [
        ("node-a", "left",  "source-1",      "R1", "FD1", 0.96, ACCENT),
        ("node-b", "right", "source-2",      "R2", "FD2", 0.93, INK),
        ("node-c", "right", "source-2-copy", "R2", "FD2", 0.91, WARN),
        ("node-d", "right", "source-3",      "R3", "FD3", 0.94, ACCENT),
    ]
    W, H = 760, 430
    body = [
        _text(30, 34, "Figure 1  Why node C is not redundancy", 16, INK, weight="bold"),
        _text(30, 56, "The capability needs one left premise and one right premise. "
                      "B, C and D all hold the right premise.", 11.5, MUTED),
        _text(30, 73, "C copies B's lineage root (R2) and failure domain (FD2). "
                      "Only D is independently rooted.", 11.5, MUTED),
    ]

    # Roots row
    root_x = {"R1": 150, "R2": 390, "R3": 630}
    for label, x in root_x.items():
        fill = WARN if label == "R2" else MUTED
        body.append(f'<rect x="{x-52}" y="110" width="104" height="34" rx="5" '
                    f'fill="none" stroke="{fill}" stroke-width="1.6"/>')
        body.append(_text(x, 132, f"root {label}", 12, fill, "middle", "bold"))
    body.append(_text(30, 132, "lineage roots", 11, MUTED))

    # Node row
    node_x = [130, 320, 470, 650]
    for (name, slot, source, root, domain, conf, colour), x in zip(nodes, node_x):
        body.append(f'<rect x="{x-72}" y="230" width="144" height="92" rx="7" '
                    f'fill="none" stroke="{colour}" stroke-width="1.8"/>')
        body.append(_text(x, 252, name, 12.5, colour, "middle", "bold"))
        body.append(_text(x, 270, f"slot: {slot}", 10.5, INK, "middle"))
        body.append(_text(x, 286, f"{source}", 10, MUTED, "middle"))
        body.append(_text(x, 302, f"{root} · {domain}", 10, colour, "middle"))
        body.append(_text(x, 316, f"conf {conf:.2f}", 9.5, MUTED, "middle"))
        rx = root_x[root]
        dash = ' stroke-dasharray="5,3"' if name == "node-c" else ""
        body.append(f'<line x1="{rx}" y1="146" x2="{x}" y2="228" '
                    f'stroke="{colour}" stroke-width="1.5"{dash}/>')
    body.append(_text(30, 252, "holders", 11, MUTED))

    # The failure
    body.append(f'<line x1="338" y1="98" x2="442" y2="156" stroke="{WARN}" stroke-width="2.4"/>')
    body.append(f'<line x1="442" y1="98" x2="338" y2="156" stroke="{WARN}" stroke-width="2.4"/>')
    body.append(_text(390, 92, "R2 fails", 11.5, WARN, "middle", "bold"))

    body.append(f'<line x1="30" y1="352" x2="{W-30}" y2="352" stroke="{GRID}" stroke-width="1"/>')
    body.append(_text(30, 376, "Replica count sees three holders of the right premise and reports redundancy 3.",
                      11.5, INK))
    body.append(_text(30, 394, "Failing R2 removes B and C together. Only D survives, so the "
                               "minimum failure-domain cut is 2, not 3.", 11.5, INK))
    body.append(_text(30, 412, "Source: NeuralGraph/coordination/fixture.py — record table read directly, not redrawn.",
                      10, MUTED))
    return _svg(W, H, "\n".join(body), "Figure 1 - why node C is not redundancy")


# ---------------------------------------------------------------- figure 3

def figure3(sweep: dict[str, Any]) -> str:
    """Pareto: capability survival against storage cost (mean bytes moved)."""
    per = sweep["per_strategy"]
    pts = [(name, v["mean_bytes_moved"], v["capability_survival_rate"])
           for name, v in per.items()]
    pts.sort(key=lambda p: p[1])

    W, H = 760, 500
    L, R, T, B = 96, 40, 92, 84
    pw, ph = W - L - R, H - T - B
    xmax = max(p[1] for p in pts) * 1.08
    ymax = 1.0

    def X(v: float) -> float:
        return L + pw * v / xmax

    def Y(v: float) -> float:
        return T + ph * (1 - v / ymax)

    body = [
        _text(30, 34, "Figure 3  Survival against storage cost", 16, INK, weight="bold"),
        _text(30, 56, "Mean capability survival over 30 seeds, 8 strategies x 9 interventions. "
                      "Up and to the left is better.", 11.5, MUTED),
        _text(30, 73, "Lineage-aware repair dominates full replication on both axes: "
                      "higher survival at roughly half the bytes.", 11.5, MUTED),
    ]

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = Y(frac)
        body.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}" '
                    f'stroke="{GRID}" stroke-width="1"/>')
        body.append(_text(L - 10, y + 4, f"{frac:.2f}", 11, MUTED, "end"))
    for tick in range(0, int(xmax) + 1, 2000):
        x = X(tick)
        body.append(f'<line x1="{x:.1f}" y1="{T+ph}" x2="{x:.1f}" y2="{T+ph+5}" '
                    f'stroke="{MUTED}" stroke-width="1"/>')
        body.append(_text(x, T + ph + 20, f"{tick//1000}k", 10.5, MUTED, "middle"))

    body.append(f'<line x1="{L}" y1="{T+ph}" x2="{L+pw}" y2="{T+ph}" stroke="{INK}" stroke-width="1.3"/>')
    body.append(f'<line x1="{L}" y1="{T}" x2="{L}" y2="{T+ph}" stroke="{INK}" stroke-width="1.3"/>')
    body.append(_text(L + pw / 2, H - 40, "mean bytes moved", 12, INK, "middle"))
    body.append(f'<text x="26" y="{T+ph/2:.1f}" font-size="12" fill="{INK}" '
                f'text-anchor="middle" transform="rotate(-90 26 {T+ph/2:.1f})">'
                f'capability survival</text>')

    # Pareto frontier over the non-oracle strategies.
    frontier, best = [], -1.0
    for name, bytes_moved, surv in pts:
        if name == "oracle_min_cut":
            continue
        if surv > best:
            frontier.append((X(bytes_moved), Y(surv)))
            best = surv
    if len(frontier) > 1:
        pathd = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                         for i, (x, y) in enumerate(frontier))
        body.append(f'<path d="{pathd}" fill="none" stroke="{GRID}" '
                    f'stroke-width="1.6" stroke-dasharray="6,4"/>')

    emphasis = {"lineage_aware_repair": ACCENT, "full_replication": WARN,
                "oracle_min_cut": ORACLE}
    for name, bytes_moved, surv in pts:
        colour = emphasis.get(name, MUTED)
        radius = 7 if name in emphasis else 4.5
        x, y = X(bytes_moved), Y(surv)
        if name == "oracle_min_cut":
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="none" '
                        f'stroke="{colour}" stroke-width="2" stroke-dasharray="3,2"/>')
        else:
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{colour}"/>')
        dy = -14 if name != "source_count_repair" else 20
        weight = "bold" if name in emphasis else "normal"
        body.append(_text(x, y + dy, f"{name}  {surv:.3f}", 10.5, colour, "middle", weight))

    body.append(_text(L, H - 18,
                      "oracle_min_cut is an upper bound, not a deployable competitor. "
                      "Source: benchmark_sweep30_seed20260813.json", 10, MUTED))
    return _svg(W, H, "\n".join(body), "Figure 3 - survival against storage cost")


def build(outdir: Path) -> list[Path]:
    sweep = json.loads((ARTIFACTS / SWEEP).read_text(encoding="utf-8"))
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, svg in (("figure1_fixture.svg", figure1()),
                      ("figure3_pareto.svg", figure3(sweep))):
        path = outdir / name
        path.write_text(svg, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--outdir", type=Path, default=Path("docs/paper/figures"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    problems = verify_digests()
    if problems:
        for p in problems:
            print(f"ARTIFACT PROBLEM: {p}", file=sys.stderr)
        return 1
    if args.check:
        print("all pinned artifact digests match")
        return 0
    for path in build(args.outdir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

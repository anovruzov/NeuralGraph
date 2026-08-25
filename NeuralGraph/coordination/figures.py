"""Render the paper's figures from the pinned artifacts, deterministically.

`docs/PAPER.md` names five figures.  Four of them (2-5) are plots of numbers
that already exist in `NeuralGraph/coordination/artifacts/*.json`; figure 1 is a
schematic of the fixture and stays hand-drawn.  This module renders those four
as SVG.

WHY SVG BY HAND AND NOT MATPLOTLIB

Every result in this repository is a byte-reproducible artifact checked against
`SHA256SUMS`, and a figure is a result: if the survival matrix moves, the
heatmap must move with it, and a reviewer must be able to regenerate the exact
file that is in the paper.  Matplotlib's SVG output embeds a version string and
font metrics resolved from the host, so it is not byte-stable across machines.
Emitting the SVG directly is: the only inputs are the artifact JSON and this
source, every number is formatted at fixed precision, and no font file is
consulted because text is positioned by anchor rather than by measured width.

The figures are therefore pinned exactly like the JSON, and
`test_figures.py` regenerates and compares them.

    python3 -m NeuralGraph.coordination.figures            # write to artifacts/figures/
    python3 -m NeuralGraph.coordination.figures --check    # verify without writing

Vector output goes into LaTeX via `\\includegraphics` after
`rsvg-convert -f pdf`, or directly with `svg.sty`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
FIGURES = ARTIFACTS / "figures"

#: Print-safe palette.  Deliberately not a rainbow: the heatmap is a single
#: sequential ramp so it survives greyscale printing, and the categorical marks
#: differ in shape as well as colour so they survive colour-blind readers.
INK = "#1b1b1f"
MUTED = "#6b7280"
GRID = "#d8dade"
ACCENT = "#1f5fa8"
WARN = "#b3441c"
RAMP_LOW = (243, 246, 249)
RAMP_HIGH = (23, 78, 143)

FONT = "'Source Sans Pro','Helvetica Neue',Helvetica,Arial,sans-serif"

#: Interventions in a fixed reading order: the five that every strategy meets
#: the same way first, then the three that separate the strategies, then the
#: partition, which is the one distribution loses (N1).  Order is part of the
#: figure's argument, so it is declared rather than sorted -- and a test asserts
#: it still covers the artifact exactly, so a new intervention cannot be dropped
#: from the figure by omission.
INTERVENTION_ORDER = [
    "node_failure",
    "memory_deletion",
    "route_removal",
    "edge_corruption",
    "stale_knowledge",
    "authorization_revocation",
    "lineage_root_failure",
    "worst_single_domain_failure",
    "network_partition",
]

#: Strategies ordered by survival, floor first, so the heatmap reads as a
#: staircase and the Pareto plot's labels do not need a legend.
STRATEGY_ORDER = [
    "isolated_local",
    "centralized",
    "fixed_distributed_replication",
    "source_count_repair",
    "full_replication",
    "random_path_diversification",
    "lineage_aware_repair",
    "oracle_min_cut",
]

SHORT_NAME = {
    "isolated_local": "isolated local",
    "centralized": "centralized",
    "fixed_distributed_replication": "fixed replication",
    "source_count_repair": "source-count repair",
    "full_replication": "full replication",
    "random_path_diversification": "random diversification",
    "lineage_aware_repair": "lineage-aware repair",
    "oracle_min_cut": "oracle min-cut",
}


def _num(value: float, places: int = 2) -> str:
    """Fixed-precision, sign-stable, and never `-0.00`."""
    text = f"{value:.{places}f}"
    return "0." + "0" * places if text.startswith("-") and float(text) == 0 else text


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _text(x: float, y: float, body: str, *, size: float = 11, anchor: str = "start",
          fill: str = INK, weight: str = "normal", rotate: float | None = None) -> str:
    transform = f' transform="rotate({_num(rotate, 1)} {_num(x, 1)} {_num(y, 1)})"' if rotate else ""
    return (
        f'<text x="{_num(x, 1)}" y="{_num(y, 1)}" font-size="{_num(size, 1)}" '
        f'text-anchor="{anchor}" fill="{fill}" font-weight="{weight}"{transform}>'
        f"{_escape(body)}</text>"
    )


def _ramp(value: float) -> str:
    """Sequential low→high ramp, linear in the value so cells are comparable."""
    channels = [
        round(low + (high - low) * max(0.0, min(1.0, value)))
        for low, high in zip(RAMP_LOW, RAMP_HIGH)
    ]
    return "#" + "".join(f"{c:02x}" for c in channels)


def _document(width: float, height: float, title: str, body: list[str]) -> str:
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width, 1)}" '
        f'height="{_num(height, 1)}" viewBox="0 0 {_num(width, 1)} {_num(height, 1)}" '
        f'font-family="{FONT}">',
        f"<title>{_escape(title)}</title>",
        f'<rect width="{_num(width, 1)}" height="{_num(height, 1)}" fill="#ffffff"/>',
    ]
    return "\n".join(head + body + ["</svg>", ""])


def _load(name: str) -> dict[str, Any]:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Figure 2 — survival matrix
# --------------------------------------------------------------------------

def figure_survival_matrix(sweep: dict[str, Any]) -> str:
    """8 strategies x 9 interventions, mean survival over the seed sweep.

    Values come from `cells`, not from `per_strategy`, so a cell and its row
    mean can be checked against each other in the figure itself.
    """
    left, top = 210.0, 118.0
    cell_w, cell_h = 74.0, 34.0
    rows, cols = len(STRATEGY_ORDER), len(INTERVENTION_ORDER)
    width = left + cell_w * cols + 96
    height = top + cell_h * rows + 74

    body = [
        _text(28, 34, "Capability survival by placement/repair strategy", size=15, weight="600"),
        _text(28, 54, "mean over 30 seeds; 1.00 = the collective still answers after the intervention",
              size=10.5, fill=MUTED),
    ]

    for c, intervention in enumerate(INTERVENTION_ORDER):
        x = left + cell_w * c + cell_w / 2
        body.append(_text(x, top - 10, intervention.replace("_", " "), size=9.5,
                          anchor="start", fill=MUTED, rotate=-38))

    for r, strategy in enumerate(STRATEGY_ORDER):
        y = top + cell_h * r
        body.append(_text(left - 12, y + cell_h / 2 + 4, SHORT_NAME[strategy], size=10.5, anchor="end"))
        for c, intervention in enumerate(INTERVENTION_ORDER):
            cell = sweep["cells"][f"{strategy}::{intervention}"]
            rate = cell["survival_rate"]
            x = left + cell_w * c
            body.append(
                f'<rect x="{_num(x, 1)}" y="{_num(y, 1)}" width="{_num(cell_w - 2, 1)}" '
                f'height="{_num(cell_h - 2, 1)}" fill="{_ramp(rate)}" stroke="{GRID}"/>'
            )
            body.append(_text(x + (cell_w - 2) / 2, y + cell_h / 2 + 4, _num(rate),
                              size=10, anchor="middle",
                              fill="#ffffff" if rate > 0.55 else INK))
        row_mean = sweep["per_strategy"][strategy]["capability_survival_rate"]
        body.append(_text(left + cell_w * cols + 10, y + cell_h / 2 + 4,
                          _num(row_mean), size=10.5, weight="600"))

    body.append(_text(left + cell_w * cols + 10, top - 10, "mean", size=9.5, fill=MUTED))
    body.append(_text(28, height - 30,
                      "Replica count does not order the rows: full replication (8 records) sits "
                      "below lineage-aware repair (4).",
                      size=10, fill=MUTED))
    body.append(_text(28, height - 14,
                      "Every strategy fails the network partition except the two that keep both "
                      "premises on one node — reported against interest.",
                      size=10, fill=MUTED))
    return _document(width, height, "Figure 2: capability survival matrix", body)


# --------------------------------------------------------------------------
# Figure 3 — Pareto: survival vs storage
# --------------------------------------------------------------------------

def figure_pareto(sweep: dict[str, Any]) -> str:
    """Survival against bytes moved. The claim is dominance, so it must be visible."""
    left, right, top, bottom = 92.0, 560.0, 96.0, 356.0
    width, height = 760.0, 470.0

    points = [
        (
            strategy,
            sweep["per_strategy"][strategy]["mean_bytes_moved"],
            sweep["per_strategy"][strategy]["capability_survival_rate"],
        )
        for strategy in STRATEGY_ORDER
    ]
    x_max = 11000.0

    def px(bytes_moved: float) -> float:
        return left + (right - left) * (bytes_moved / x_max)

    def py(rate: float) -> float:
        return bottom - (bottom - top) * rate

    body = [
        _text(28, 34, "Survival is not bought with storage", size=15, weight="600"),
        _text(28, 54, "mean bytes moved per intervention vs mean capability survival, 30 seeds",
              size=10.5, fill=MUTED),
    ]

    for tick in range(0, 6):
        rate = tick / 5
        y = py(rate)
        body.append(f'<line x1="{_num(left, 1)}" y1="{_num(y, 1)}" x2="{_num(right, 1)}" '
                    f'y2="{_num(y, 1)}" stroke="{GRID}"/>')
        body.append(_text(left - 10, y + 4, _num(rate, 1), size=10, anchor="end", fill=MUTED))
    for tick in range(0, 6):
        value = x_max * tick / 5
        x = px(value)
        body.append(f'<line x1="{_num(x, 1)}" y1="{_num(top, 1)}" x2="{_num(x, 1)}" '
                    f'y2="{_num(bottom, 1)}" stroke="{GRID}"/>')
        body.append(_text(x, bottom + 18, f"{value / 1000:.1f}k", size=10, anchor="middle", fill=MUTED))

    body.append(_text((left + right) / 2, bottom + 40, "mean bytes moved", size=11, anchor="middle"))
    body.append(_text(26, (top + bottom) / 2, "capability survival", size=11, anchor="middle",
                      rotate=-90))

    # The dominance shading: everything below-right of lineage-aware repair is
    # strictly worse on both axes. Drawing it is what turns two scatter points
    # into a claim.
    lineage = next(p for p in points if p[0] == "lineage_aware_repair")
    body.append(
        f'<rect x="{_num(px(lineage[1]), 1)}" y="{_num(py(lineage[2]), 1)}" '
        f'width="{_num(right - px(lineage[1]), 1)}" height="{_num(bottom - py(lineage[2]), 1)}" '
        f'fill="{ACCENT}" opacity="0.06"/>'
    )
    body.append(_text(px(lineage[1]) + 10, bottom - 12,
                      "dominated: more bytes, less survival", size=9.5, fill=ACCENT))

    placed_labels: list[float] = []
    for strategy, bytes_moved, rate in points:
        x, y = px(bytes_moved), py(rate)
        highlight = strategy in ("lineage_aware_repair", "full_replication")
        colour = ACCENT if strategy == "lineage_aware_repair" else (
            WARN if strategy == "full_replication" else INK)
        if strategy == "oracle_min_cut":
            body.append(
                f'<polygon points="{_num(x, 1)},{_num(y - 6, 1)} {_num(x + 6, 1)},{_num(y + 5, 1)} '
                f'{_num(x - 6, 1)},{_num(y + 5, 1)}" fill="none" stroke="{MUTED}" stroke-width="1.4"/>'
            )
        else:
            body.append(
                f'<circle cx="{_num(x, 1)}" cy="{_num(y, 1)}" r="{_num(5.5 if highlight else 4, 1)}" '
                f'fill="{colour}"/>'
            )
        anchor, dx = ("end", -10) if bytes_moved > 6000 else ("start", 10)
        # Two strategies sit on the same survival at nearly the same cost, so
        # their labels would print on top of each other. Nudge any label that
        # lands within a line height of one already placed.
        label_y = y + 4
        while any(abs(label_y - placed) < 12 for placed in placed_labels):
            label_y += 12
        placed_labels.append(label_y)
        body.append(_text(x + dx, label_y, SHORT_NAME[strategy], size=10, anchor=anchor,
                          fill=colour, weight="600" if highlight else "normal"))

    body.append(_text(28, height - 46,
                      "Lineage-aware repair reaches 0.78 at 5.4k bytes; full replication reaches "
                      "0.67 at 9.9k — twice the storage, less survival.", size=10, fill=MUTED))
    body.append(_text(28, height - 30,
                      "The oracle (open triangle) is an upper bound, not a deployable policy: it "
                      "separates from lineage-aware repair on one intervention.", size=10, fill=MUTED))
    body.append(_text(28, height - 14,
                      "Survival averages 9 interventions including the partition, which no "
                      "distributed strategy survives.", size=10, fill=MUTED))
    return _document(width, height, "Figure 3: survival versus storage", body)


# --------------------------------------------------------------------------
# Figure 4 — scale invariance
# --------------------------------------------------------------------------

def _parse_spec(spec: dict[str, Any]) -> tuple[int, int, int]:
    """(premises per capability, holders per premise, independent roots).

    Read from the spec's own fields rather than parsed out of its `label`, so a
    relabelling cannot silently redraw the axis.
    """
    return spec["slots"], spec["holders_per_slot"], spec["independent_roots"]


def figure_scale(scale: dict[str, Any]) -> str:
    """Every scale point's verdict, as a grid.

    The claim is a null result -- nothing moves with scale -- and a line chart
    of two dozen flat, coincident lines argues it badly: the reader cannot tell
    a bundle of identical lines from an artefact of drawing. So every one of the
    96 points is drawn as its own verdict mark, rows being the conditions that
    do change the verdict and columns being the scale parameters that do not.
    Invariance is then visible as rows of uniform colour, and a single
    exception would be visible as one odd cell.
    """
    ks = sorted({point["spec"]["slots"] for point in scale["points"]})
    hs = sorted({point["spec"]["holders_per_slot"] for point in scale["points"]})
    policies = sorted({point["repair_policy"] for point in scale["points"]})
    interventions = sorted({point["intervention"] for point in scale["points"]})
    independences = sorted({point["spec"]["independent_roots"] for point in scale["points"]})

    verdicts: dict[tuple, bool] = {}
    for point in scale["points"]:
        spec = point["spec"]
        verdicts[(
            point["repair_policy"], point["intervention"], spec["independent_roots"],
            spec["slots"], spec["holders_per_slot"],
        )] = point["capability_survival"]

    rows = [
        (policy, intervention, independence)
        for policy in policies
        for independence in independences
        for intervention in interventions
    ]
    cols = [(k, h) for k in ks for h in hs]

    left, top = 268.0, 132.0
    cell = 30.0
    width = left + cell * len(cols) + 150
    height = top + cell * len(rows) + 92

    body = [
        _text(28, 34, "Every verdict is invariant in scale", size=15, weight="600"),
        _text(28, 54, "one mark per run: filled = capability survived the intervention, "
                      "hollow = lost", size=10.5, fill=MUTED),
    ]

    for index, (k, h) in enumerate(cols):
        x = left + cell * index + cell / 2
        body.append(_text(x, top - 26, f"K={k}", size=10, anchor="middle", fill=MUTED))
        body.append(_text(x, top - 12, f"H={h}", size=9.5, anchor="middle", fill=MUTED))

    for index, (policy, intervention, independence) in enumerate(rows):
        y = top + cell * index
        label = f"{policy.replace('_', '-')} · {intervention.replace('_', ' ')}"
        body.append(_text(left - 46, y + cell / 2 + 4, label, size=10, anchor="end"))
        colour = ACCENT if independence == max(independences) else WARN
        body.append(_text(left - 12, y + cell / 2 + 4, f"I{independence}", size=10,
                          anchor="end", fill=colour, weight="600"))
        for col, (k, h) in enumerate(cols):
            survived = verdicts[(policy, intervention, independence, k, h)]
            cx, cy = left + cell * col + cell / 2, y + cell / 2
            body.append(
                f'<circle cx="{_num(cx, 1)}" cy="{_num(cy, 1)}" r="7" '
                f'fill="{colour if survived else "#ffffff"}" stroke="{colour}" '
                f'stroke-width="1.3"/>'
            )
        rate = sum(
            1 for k, h in cols if verdicts[(policy, intervention, independence, k, h)]
        ) / len(cols)
        body.append(_text(left + cell * len(cols) + 14, y + cell / 2 + 4, _num(rate),
                          size=10.5, weight="600"))

    body.append(_text(left + cell * len(cols) + 14, top - 12, "survival", size=9.5, fill=MUTED))
    body.append(_text(28, height - 62,
                      "Rows are policy x intervention x lineage independence; columns are premises "
                      "per capability (K) and holders per premise (H).", size=10, fill=MUTED))
    body.append(_text(28, height - 46,
                      "Every row is uniform: the verdict is set by lineage independence, never by "
                      "K or H. One exception would show as a single odd mark.", size=10, fill=MUTED))
    body.append(_text(28, height - 30,
                      "The one I1 row that survives is source-count repair under node failure: a "
                      "correlated replica serves when the node, not the lineage, is what failed.",
                      size=10, fill=WARN))
    body.append(_text(28, height - 14,
                      f"{len(rows) * len(cols)} runs, seed {scale['seed']}, from "
                      "scale_seed20260813.json.", size=10, fill=MUTED))
    return _document(width, height, "Figure 4: scale invariance", body)


# --------------------------------------------------------------------------
# Figure 5 — silent forgetting
# --------------------------------------------------------------------------

def figure_silent_forgetting(benchmark: dict[str, Any]) -> str:
    """Where the coordinator's confidence sits when the capability is gone.

    A scatter of confidence against coverage collapses here: the cells take
    only three coverage values, so almost every mark lands on top of another
    and the picture argues nothing. What carries the claim is the *ordering* --
    the cells that lost the capability report higher confidence than the cells
    that kept it -- so the figure is a strip plot of confidence with the cells
    split by outcome, and the two distributions do not overlap in the direction
    a monitor would need.
    """
    left, right = 250.0, 600.0
    width, height = 760.0, 372.0
    x_min, x_max = 0.88, 1.0

    lanes: list[tuple[str, str, str]] = [
        ("capability intact", "kept", ACCENT),
        ("lost, silently: still confident", "silent", WARN),
        ("lost, visibly: no answer offered", "visible", MUTED),
    ]
    counts: dict[tuple[str, float], int] = {}
    for key in sorted(benchmark["cells"]):
        cell = benchmark["cells"][key]
        confidence = cell["post_intervention_confidence"]
        if cell["capability_survival"]:
            lane = "kept"
        elif confidence <= 0.0:
            lane = "visible"
        else:
            lane = "silent"
        counts[(lane, confidence)] = counts.get((lane, confidence), 0) + 1

    def px(confidence: float) -> float:
        return left + (right - left) * (confidence - x_min) / (x_max - x_min)

    body = [
        _text(28, 34, "Lost capabilities report the highest confidence of all",
              size=15, weight="600"),
        _text(28, 54, "post-intervention confidence per strategy x intervention cell, "
                      "single pinned seed", size=10.5, fill=MUTED),
    ]

    top = 118.0
    lane_h = 46.0
    for tick in range(0, 7):
        confidence = x_min + (x_max - x_min) * tick / 6
        x = px(confidence)
        body.append(f'<line x1="{_num(x, 1)}" y1="{_num(top - 24, 1)}" x2="{_num(x, 1)}" '
                    f'y2="{_num(top + lane_h * len(lanes) - 12, 1)}" stroke="{GRID}"/>')
        body.append(_text(x, top + lane_h * len(lanes) + 6, _num(confidence),
                          size=10, anchor="middle", fill=MUTED))

    for index, (label, lane, colour) in enumerate(lanes):
        y = top + lane_h * index
        body.append(_text(left - 20, y + 4, label, size=10.5, anchor="end", fill=colour,
                          weight="600" if lane == "silent" else "normal"))
        marks = sorted((c, n) for (lane_name, c), n in counts.items() if lane_name == lane)
        total = sum(n for _c, n in marks)
        body.append(_text(left - 20, y + 19, f"{total} cells", size=9.5, anchor="end", fill=MUTED))
        for confidence, count in marks:
            if confidence <= 0.0:
                # Off-scale by construction: these cells answer nothing at all,
                # which is the honest failure. Park them at the axis start and
                # say so rather than stretching the axis to zero and flattening
                # the region where the argument actually happens.
                x = left - 2
                body.append(_text(x, y + 4, f"{count} at confidence 0.00", size=10,
                                  anchor="start", fill=MUTED))
                continue
            x = px(confidence)
            body.append(
                f'<circle cx="{_num(x, 1)}" cy="{_num(y, 1)}" r="{_num(6 + count * 0.55, 1)}" '
                f'fill="{colour}" opacity="0.55"/>'
            )
            body.append(_text(x, y + 3.5, str(count), size=9, anchor="middle", fill="#ffffff"))

    body.append(_text((left + right) / 2, top + lane_h * len(lanes) + 30,
                      "post-intervention confidence", size=11, anchor="middle"))

    body.append(_text(28, height - 62,
                      "Marks are sized and labelled by the number of cells sharing that "
                      "confidence.", size=10, fill=MUTED))
    body.append(_text(28, height - 46,
                      "The two lanes occupy the same range, 0.91-0.96, and the highest confidence "
                      "in the figure is reported by cells that lost the capability.",
                      size=10, fill=MUTED))
    body.append(_text(28, height - 30,
                      "No confidence threshold separates the two lanes, so no monitor reading "
                      "confidence can detect the loss.", size=10, fill=WARN))
    body.append(_text(28, height - 14,
                      "The visibly lost cells are the isolated-local baseline: it offers no answer, "
                      "which is a failure a monitor can see.", size=10, fill=MUTED))
    return _document(width, height, "Figure 5: silent forgetting", body)


# --------------------------------------------------------------------------

def render_all() -> dict[str, str]:
    """Every figure, keyed by filename, in the order the paper uses them."""
    sweep = _load("benchmark_sweep30_seed20260813.json")
    benchmark = _load("benchmark_seed20260813.json")
    scale = _load("scale_seed20260813.json")
    return {
        "fig2_survival_matrix.svg": figure_survival_matrix(sweep),
        "fig3_pareto_survival_vs_storage.svg": figure_pareto(sweep),
        "fig4_scale_invariance.svg": figure_scale(scale),
        "fig5_silent_forgetting.svg": figure_silent_forgetting(benchmark),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output-dir", type=Path, default=FIGURES)
    parser.add_argument("--check", action="store_true",
                        help="compare against what is committed; write nothing")
    args = parser.parse_args(argv)

    figures = render_all()
    if args.check:
        stale = [
            name for name, svg in sorted(figures.items())
            if not (args.output_dir / name).exists()
            or (args.output_dir / name).read_text(encoding="utf-8") != svg
        ]
        for name in stale:
            print(f"stale: {name}", file=sys.stderr)
        if stale:
            print("Figures do not match their artifacts. Regenerate deliberately.",
                  file=sys.stderr)
            return 1
        print(f"{len(figures)} figures match their artifacts.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, svg in sorted(figures.items()):
        (args.output_dir / name).write_text(svg, encoding="utf-8")
        print(f"wrote {args.output_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Summarise artifacts/loss_funnel.jsonl into the tables LOSS_ACCOUNTING.md
is built from.

Everything here is a count over per-pattern rows; nothing is modelled.

    python3 -m research.mycelic.loss_report            # print markdown
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Sequence

import numpy as np

from .analysis import boot_ci
from .loss_accounting import FLAT_STAGES, FUNNEL, HIER_STAGES

STAGE_LABEL = {
    "extracted": "extracted (>=2 facets, right entity+predicate)",
    "sketch_visible": "visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2)",
    "in_triage": "on the triage candidate list",
    "questioned": "inside the question budget",
    "descent_reached": "descent reached a facet holder",
    "in_pool": ">=2 gold links in kernel pool (= evidence coverage)",
    "candidate": "candidate formed on right entity + chain",
    "matched_any": "matched primary rule at ANY confidence",
    "matched_tau": "matched with confidence >= 0.5",
    "in_register": "survived the register cut (= reported)",
}

SKETCH_FAIL_LABEL = {
    "no_site_bit": "no site set a predicate bit (support threshold)",
    "foreign_lt2": "fewer than 2 foreign sites",
    "regions_lt2": "foreign sites in fewer than 2 regions",
    "span_lt2": "causal span < 2 across foreign sites",
}


def load(path: str = FUNNEL) -> List[Dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def stages_for(arch: str) -> List[str]:
    return HIER_STAGES if arch.startswith(("H_", "G_", "F_", "E_", "J_", "I_", "V")) \
        else FLAT_STAGES


def survival_table(rows: List[Dict], scale: int, subset: str = "all") -> str:
    """Stage-by-stage survival per architecture, mean over seeds with a
    bootstrap interval, for all / rare / common patterns."""
    pr = [r for r in rows if not r.get("summary") and r["scale"] == scale]
    if subset == "rare":
        pr = [r for r in pr if r["rare"]]
    elif subset == "common":
        pr = [r for r in pr if not r["rare"]]
    archs = sorted({r["arch"] for r in pr})
    if not archs:
        return "_(no rows)_"
    all_stages = [s for s in HIER_STAGES]
    head = "| stage | " + " | ".join(archs) + " |"
    sep = "|---|" + "---:|" * len(archs)
    lines = [head, sep]
    for s in all_stages:
        cells = []
        for a in archs:
            if s not in stages_for(a):
                cells.append("—")
                continue
            # per-seed fraction, then bootstrap over seeds
            seeds = sorted({r["seed"] for r in pr if r["arch"] == a})
            fr = []
            for sd in seeds:
                sub = [r for r in pr if r["arch"] == a and r["seed"] == sd]
                if sub:
                    fr.append(sum(1 for r in sub if r[s]) / len(sub))
            m, lo, hi = boot_ci(fr)
            cells.append(f"{m:.3f} <sub>[{lo:.2f}, {hi:.2f}]</sub>")
        lines.append(f"| {STAGE_LABEL[s]} | " + " | ".join(cells) + " |")
    n = {a: len({(r['seed'], r['pid']) for r in pr if r['arch'] == a}) for a in archs}
    lines.append("")
    lines.append("patterns counted: " + ", ".join(f"{a}: {n[a]}" for a in archs))
    return "\n".join(lines)


def first_loss_table(rows: List[Dict], scale: int, arch: str) -> str:
    """Where patterns die FIRST, all / rare / common."""
    pr = [r for r in rows if not r.get("summary") and r["scale"] == scale
          and r["arch"] == arch]
    if not pr:
        return "_(no rows)_"
    stages = stages_for(arch) + ["reported"]
    lines = ["| first stage lost | all | rare | common |", "|---|---:|---:|---:|"]
    for s in stages:
        key = "" if s == "reported" else s
        allc = sum(1 for r in pr if r["first_loss"] == key)
        rc = sum(1 for r in pr if r["first_loss"] == key and r["rare"])
        cc = allc - rc
        lab = STAGE_LABEL.get(s, "**reported (survived every stage)**")
        lines.append(f"| {lab} | {allc} ({allc / len(pr):.0%}) | {rc} | {cc} |")
    lines.append(f"| total | {len(pr)} | {sum(1 for r in pr if r['rare'])} | "
                 f"{sum(1 for r in pr if not r['rare'])} |")
    return "\n".join(lines)


def sketch_fail_table(rows: List[Dict], scale: int, arch: str = "H_mycelic_full") -> str:
    pr = [r for r in rows if not r.get("summary") and r["scale"] == scale
          and r["arch"] == arch and "sketch_fail" in r and not r["sketch_visible"]]
    if not pr:
        return "_(no sketch-invisible patterns)_"
    lines = ["| why the sketch could not see it | all | rare | common | "
             "median witnesses |", "|---|---:|---:|---:|---:|"]
    for k, lab in SKETCH_FAIL_LABEL.items():
        sub = [r for r in pr if r["sketch_fail"] == k]
        if not sub:
            continue
        lines.append(f"| {lab} | {len(sub)} | {sum(1 for r in sub if r['rare'])} | "
                     f"{sum(1 for r in sub if not r['rare'])} | "
                     f"{int(np.median([r['n_witnesses'] for r in sub]))} |")
    return "\n".join(lines)


def witness_curve(rows: List[Dict], scale: int, arch: str) -> str:
    """Discovery as a function of how many people witnessed the pattern."""
    pr = [r for r in rows if not r.get("summary") and r["scale"] == scale
          and r["arch"] == arch]
    if not pr:
        return ""
    bins = [(0, 4), (5, 7), (8, 11), (12, 16), (17, 25), (26, 10 ** 6)]
    lines = ["| witnesses (records) | n | visible to sketch | in pool | reported |",
             "|---|---:|---:|---:|---:|"]
    for lo, hi in bins:
        sub = [r for r in pr if lo <= r["n_witnesses"] <= hi]
        if not sub:
            continue
        sv = (f"{sum(1 for r in sub if r['sketch_visible']) / len(sub):.2f}"
              if "sketch_visible" in sub[0] else "—")
        lines.append(
            f"| {lo}–{'∞' if hi > 10 ** 5 else hi} | {len(sub)} | {sv} | "
            f"{sum(1 for r in sub if r['in_pool']) / len(sub):.2f} | "
            f"{sum(1 for r in sub if r['in_register']) / len(sub):.2f} |")
    return "\n".join(lines)


def rank_table(rows: List[Dict], scale: int) -> str:
    """Of the patterns that ARE reported, where in the register do they sit?"""
    pr = [r for r in rows if not r.get("summary") and r["scale"] == scale
          and r["in_register"]]
    archs = sorted({r["arch"] for r in pr})
    lines = ["| architecture | reported | median rank | within top 40 | "
             "within top 100 | median confidence |", "|---|---:|---:|---:|---:|---:|"]
    for a in archs:
        sub = [r for r in pr if r["arch"] == a]
        rk = [r["rank"] for r in sub if r["rank"] >= 0]
        lines.append(
            f"| {a} | {len(sub)} | {int(np.median(rk)) if rk else '—'} | "
            f"{sum(1 for x in rk if x < 40)} | {sum(1 for x in rk if x < 100)} | "
            f"{np.median([r['match_conf'] for r in sub]):.2f} |")
    return "\n".join(lines)


def gap_decomposition(rows: List[Dict], scale: int,
                      a: str = "H_mycelic_full", b: str = "A2_chunked_ctx") -> str:
    """Decompose the discovery gap between two systems into the stages that
    account for it, on the SAME patterns (paired by seed and pid)."""
    pa = {(r["seed"], r["pid"]): r for r in rows
          if not r.get("summary") and r["scale"] == scale and r["arch"] == a}
    pb = {(r["seed"], r["pid"]): r for r in rows
          if not r.get("summary") and r["scale"] == scale and r["arch"] == b}
    keys = sorted(set(pa) & set(pb))
    if not keys:
        return "_(no paired rows)_"
    n = len(keys)
    a_rep = sum(1 for k in keys if pa[k]["in_register"])
    b_rep = sum(1 for k in keys if pb[k]["in_register"])
    # patterns B reports and A does not: where did A lose them?
    lost = [pa[k] for k in keys if pb[k]["in_register"] and not pa[k]["in_register"]]
    won = [k for k in keys if pa[k]["in_register"] and not pb[k]["in_register"]]
    st: Dict[str, int] = {}
    for r in lost:
        st[r["first_loss"]] = st.get(r["first_loss"], 0) + 1
    lines = [f"Paired on {n} (seed, pattern) pairs at {scale:,} users: "
             f"`{b}` reports {b_rep} ({b_rep / n:.1%}), `{a}` reports {a_rep} "
             f"({a_rep / n:.1%}). `{b}` finds {len(lost)} that `{a}` misses; "
             f"`{a}` finds {len(won)} that `{b}` misses.", "",
             f"**Of the {len(lost)} patterns `{b}` reports and `{a}` does not, "
             f"the first stage `{a}` lost them at:**", "",
             "| stage | patterns | share of the gap | of which rare |",
             "|---|---:|---:|---:|"]
    for s in stages_for(a):
        c = st.get(s, 0)
        if c == 0:
            continue
        rc = sum(1 for r in lost if r["first_loss"] == s and r["rare"])
        lines.append(f"| {STAGE_LABEL[s]} | {c} | {c / max(1, len(lost)):.0%} | {rc} |")
    return "\n".join(lines)


def build(scales: Sequence[int] = (10_000, 50_000)) -> str:
    rows = load()
    if not rows:
        return "_(loss_funnel.jsonl not found — run loss_accounting first)_"
    out = []
    for sc in scales:
        if not any(r["scale"] == sc for r in rows):
            continue
        n_seeds = len({r["seed"] for r in rows if r["scale"] == sc})
        out.append(f"## {sc:,} users ({n_seeds} seeds)\n")
        out.append("### Stage survival, all patterns\n")
        out.append(survival_table(rows, sc, "all"))
        out.append("\n### Stage survival, rare-signal patterns only\n")
        out.append(survival_table(rows, sc, "rare"))
        out.append("\n### Stage survival, common patterns only\n")
        out.append(survival_table(rows, sc, "common"))
        out.append("\n### Where the hierarchy loses each pattern FIRST\n")
        out.append(first_loss_table(rows, sc, "H_mycelic_full"))
        out.append("\n### Why the sketch could not see the invisible ones\n")
        out.append(sketch_fail_table(rows, sc))
        out.append("\n### Discovery by number of witnesses (hierarchy)\n")
        out.append(witness_curve(rows, sc, "H_mycelic_full"))
        out.append("\n### Discovery by number of witnesses (A2_chunked_ctx)\n")
        out.append(witness_curve(rows, sc, "A2_chunked_ctx"))
        out.append("\n### Where the reported patterns sit in the register\n")
        out.append(rank_table(rows, sc))
        out.append("\n### Decomposing the gap to the strongest centralised system\n")
        out.append(gap_decomposition(rows, sc))
        out.append("\n### Decomposing the gap to the centralised twin (same algorithm)\n")
        out.append(gap_decomposition(rows, sc, b="B4_central_triage"))
        out.append("\n### Decomposing the gap to the perfect-retrieval oracle\n")
        out.append(gap_decomposition(rows, sc, b="Y_oracle_retrieval"))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(build())

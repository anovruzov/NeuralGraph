"""Every number the vNext deliverables quote, computed from raw artifacts.

Nothing in here is typed in by hand: the OLD rows come from artifacts/v1/
(the benchmark as it was before the vNext work, archived by final_rerun.sh),
the NEW rows from artifacts/, the change ledger from the paired experiment
files artifacts/quick_<tag>.jsonl, the funnels from loss_funnel.jsonl in
both places, the ranker evaluation from ranker_eval_final.json and the
frozen configuration from calibration.json's provenance block.

    python3 -m research.mycelic.vnext_data      # prints every table
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .analysis import boot_ci, dedupe, sign_test
from .runner import ART

V1 = os.path.join(ART, "v1")

METRICS = [
    ("found_anywhere_in_register", "found", True),
    ("evidence_coverage_2links", "evidence cov.", True),
    ("rare_signal_recall", "rare recall", True),
    ("common_signal_recall", "common recall", True),
    ("average_precision", "AP", True),
    ("false_discovery_rate", "FDR", False),
    ("decoy_acceptance_all", "decoy acc. (all)", False),
    ("decoy_D1_entity_coincidence", "decoy D1 entity", False),
    ("decoy_D2_temporal_scramble", "decoy D2 scramble", False),
    ("decoy_D3_near_miss_entity", "decoy D3 near-miss", False),
    ("decoy_D5_stale_chain", "decoy D5 stale", False),
    ("compute_units", "compute", False),
    ("inference_calls", "calls (metering)", False),
    ("max_context_tokens", "kernel prompt tokens (max)", False),
]
CORE = ["found_anywhere_in_register", "evidence_coverage_2links", "rare_signal_recall",
        "average_precision", "false_discovery_rate", "decoy_acceptance_all",
        "decoy_D5_stale_chain", "decoy_D2_temporal_scramble",
        "compute_units", "inference_calls", "max_context_tokens"]
DECOYS = ["decoy_D1_entity_coincidence", "decoy_D2_temporal_scramble",
          "decoy_D3_near_miss_entity", "decoy_D5_stale_chain"]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _rows(fname: str, base: str = ART) -> List[Dict]:
    p = os.path.join(base, fname)
    if not os.path.exists(p):
        return []
    with open(p) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def e1_rows(base: str = ART) -> List[Dict]:
    """Headline rows: the first row per (arch, scale, seed), as the report reads them."""
    return dedupe(_rows("e1_baselines.jsonl", base) + _rows("e1b_extra.jsonl", base))


def by_seed(rows: List[Dict], arch: str, scale: int) -> Dict[int, Dict]:
    return {r["seed"]: r for r in rows if r["arch"] == arch and r["scale"] == scale}


def _fmt(key: str, v: float) -> str:
    if key in ("compute_units", "inference_calls", "max_context_tokens"):
        return f"{v:.2e}"
    return f"{v:.3f}"


def _verdict(d: np.ndarray, higher_better: bool) -> Tuple[str, str, str, str]:
    """(mean delta, ci text, wins text, verdict) for a paired difference vector."""
    if len(d) == 0:
        return "—", "—", "—", "no rows"
    m, lo, hi = boot_ci(d)
    k, n, p = sign_test(d.tolist())
    wins = k if higher_better else (n - k)
    if abs(m) < 1e-12:
        v = "same"
    elif (lo > 0 and higher_better) or (hi < 0 and not higher_better):
        v = "**better**"
    elif (hi < 0 and higher_better) or (lo > 0 and not higher_better):
        v = "**worse**"
    else:
        v = "inside noise"
    if len(d) < 5:
        lo, hi = float(np.min(d)), float(np.max(d))
        ci = f"[{lo:+.3f}, {hi:+.3f}] (range)"
    else:
        ci = f"[{lo:+.3f}, {hi:+.3f}]"
    return f"{m:+.3f}", ci, f"{wins}/{n}", v


def paired_rows(a: Dict[int, Dict], b: Dict[int, Dict], keys: Sequence[str] = CORE
                ) -> Dict[str, Tuple[float, float, np.ndarray]]:
    """Per metric: (mean a, mean b, paired deltas b-a) over the common seeds."""
    seeds = sorted(set(a) & set(b))
    out = {}
    for k in keys:
        va = np.array([a[s][k] for s in seeds], dtype=float)
        vb = np.array([b[s][k] for s in seeds], dtype=float)
        out[k] = (float(va.mean()) if len(seeds) else float("nan"),
                  float(vb.mean()) if len(seeds) else float("nan"), vb - va)
    return out


# ---------------------------------------------------------------------------
# OLD vs NEW
# ---------------------------------------------------------------------------

def old_new_table(scale: int,
                  archs: Sequence[Tuple[str, str, str]] = (
                      ("Mycelic hierarchy", "H_mycelic_full", "H_mycelic_full"),
                      ("A2 chunked long context", "A2_chunked_ctx", "A2_chunked_ctx"),
                      ("B4 central triage", "B4_central_triage", "B4_central_triage"),
                      ("Y oracle retrieval", "Y_oracle_retrieval", "Y_oracle_retrieval")),
                  keys: Sequence[str] = CORE,
                  seeds: Optional[Sequence[int]] = None) -> str:
    """OLD (artifacts/v1) against NEW (artifacts) on the same worlds.  `seeds`
    restricts both sides, e.g. to the confirmation panel 5-9 that no
    development decision ever read."""
    old = e1_rows(V1)
    new = e1_rows(ART)
    if seeds is not None:
        old = [r for r in old if r["seed"] in seeds]
        new = [r for r in new if r["seed"] in seeds]
    if not old or not new:
        return "_(old or new headline rows missing: run final_rerun.sh)_"
    label = {k: l for k, l, _ in METRICS}
    hb = {k: h for k, _, h in METRICS}
    lines = ["| system | metric | OLD | NEW | Δ | 95% CI | better on | verdict |",
             "|---|---|---:|---:|---:|---|---:|---|"]
    for name, oa, na in archs:
        a = by_seed(old, oa, scale)
        b = by_seed(new, na, scale)
        if not a or not b:
            lines.append(f"| {name} | — | — | — | — | — | — | no rows |")
            continue
        pr = paired_rows(a, b, keys)
        for k in keys:
            ma, mb, d = pr[k]
            dm, ci, wins, v = _verdict(d, hb[k])
            if k in ("compute_units", "inference_calls", "max_context_tokens"):
                dm = f"{(mb / ma - 1) * 100:+.0f}%" if ma else "—"
            lines.append(f"| {name} | {label[k]} | {_fmt(k, ma)} | {_fmt(k, mb)} | "
                         f"{dm} | {ci} | {wins} | {v} |")
    n_old = len(set(by_seed(old, archs[0][1], scale)))
    n_new = len(set(by_seed(new, archs[0][2], scale)))
    lines.append("")
    lines.append(f"paired seeds at {scale:,}: {min(n_old, n_new)} "
                 f"(old rows {n_old}, new rows {n_new}); the register cap is "
                 f"{min(6000, max(600, scale // 10 if scale >= 10_000 else 600))} entries "
                 f"in both.")
    return "\n".join(lines)


def prev_reproduction_table(scale: int) -> str:
    """H_mycelic_prev inside the NEW suite against the archived OLD H rows:
    the v1 configuration should reproduce, and this is the check."""
    old = by_seed(e1_rows(V1), "H_mycelic_full", scale)
    new = by_seed(e1_rows(ART), "H_mycelic_prev", scale)
    if not old or not new:
        return "_(no H_mycelic_prev rows yet)_"
    pr = paired_rows(old, new, CORE)
    label = {k: l for k, l, _ in METRICS}
    lines = ["| metric | OLD H_mycelic_full | NEW H_mycelic_prev | max abs. Δ over seeds |",
             "|---|---:|---:|---:|"]
    for k in CORE:
        ma, mb, d = pr[k]
        lines.append(f"| {label[k]} | {_fmt(k, ma)} | {_fmt(k, mb)} | "
                     f"{_fmt(k, float(np.max(np.abs(d))) if len(d) else float('nan'))} |")
    return "\n".join(lines)


def gap_table(scale: int, hier: str = "H_mycelic_full",
              controls: Sequence[str] = ("A2_chunked_ctx", "B4_central_triage",
                                         "Y_oracle_retrieval", "H_mycelic_lean",
                                         "H_mycelic_prev")) -> str:
    """NEW hierarchy against every control on the same NEW worlds."""
    new = e1_rows(ART)
    h = by_seed(new, hier, scale)
    if not h:
        return "_(no new rows yet)_"
    lines = ["| system | found | evidence cov. | rare recall | AP | decoy acc. | D5 stale | compute | calls | kernel prompt (max tokens) | found gap to H | compute ratio to H |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]

    def row(name, d):
        seeds = sorted(d)
        m = lambda k: float(np.mean([d[s][k] for s in seeds]))
        common = sorted(set(seeds) & set(h))
        gap = float(np.mean([d[s]["found_anywhere_in_register"] - h[s]["found_anywhere_in_register"]
                             for s in common])) if common else float("nan")
        cr = m("compute_units") / max(1e-9, float(np.mean([h[s]["compute_units"] for s in seeds if s in h]))) \
            if any(s in h for s in seeds) else float("nan")
        lines.append(f"| {name} | {m('found_anywhere_in_register'):.3f} | {m('evidence_coverage_2links'):.3f} | "
                     f"{m('rare_signal_recall'):.3f} | {m('average_precision'):.3f} | {m('decoy_acceptance_all'):.3f} | "
                     f"{m('decoy_D5_stale_chain'):.3f} | {m('compute_units'):.2e} | {m('inference_calls'):.2e} | "
                     f"{m('max_context_tokens'):.2e} | {gap:+.3f} | {cr:.2f}x |")
    row(hier, h)
    for c in controls:
        d = by_seed(new, c, scale)
        if d:
            row(c, d)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# change ledger from the paired experiment files
# ---------------------------------------------------------------------------

def _quick(tag: str) -> List[Dict]:
    return _rows(f"quick_{tag}.jsonl")


def _side(tag: str, sel) -> Dict[int, Dict]:
    """One arm of a paired file.  quick_paired files interleave base/variant
    rows under one label when the variant is a ranker switch, so an int
    selects by row parity (0 = base, 1 = variant); a string selects the
    arm_paired label.  A tag 'a|b' names two files, one per side."""
    rows = _quick(tag)
    if isinstance(sel, int):
        return {r["seed"]: r for i, r in enumerate(rows) if i % 2 == sel}
    return {r["seed"]: r for r in rows if r["arch"] == sel}


def _arms(tag: str, base, variant) -> Tuple[Dict[int, Dict], Dict[int, Dict]]:
    ta, _, tb = tag.partition("|")
    tb = tb or ta
    if base is None:
        base, variant = 0, 1
    return _side(ta, base), _side(tb, variant)


# (tag, base label or None for interleaved, variant label, scale, change,
#  status, evidence seeds)
LEDGER: List[Tuple] = [
    ("rk_H", None, None, 10_000, "ranker v1: learned order inside the hand gate", "superseded", "eval 0-4"),
    ("rk2_H", None, None, 10_000, "ranker v2: learned top-K, anchor-context features", "superseded", "eval 0-4"),
    ("v3_H", None, None, 10_000, "ranker v3 + question_frac 0.65 + batched descent", "ACCEPTED", "eval 0-4"),
    ("seldw_H", "v3", "v3dw", 10_000, "decoy-weighted ranker selection", "rejected", "eval 0-4"),
    ("qf_v3", "qf0.65", "qf0.80", 10_000, "question_frac 0.80 (vs 0.65)", "rejected", "eval 0-4"),
    ("qf_v3", "qf0.65", "qf1.00", 10_000, "question_frac 1.00 (vs 0.65)", "rejected", "eval 0-4"),
    ("rx_base065|rx_reextract", 1, 1, 10_000, "local re-extraction at the user (vs qf 0.65 batched)", "rejected", "eval 0-4"),
    ("rk2_qf0.65|rx_base065", 1, 1, 10_000, "batched descent metering alone (ranker v2, qf 0.65; same decisions)", "ACCEPTED", "eval 0-4"),
    ("refit_mod", "v3", "mod_refit", 10_000, "modal link timing (+ refitted ranker)", "rejected", "eval 0-4"),
    ("refit_hyb", "v3", "hyb_refit", 10_000, "hybrid link timing (+ refitted ranker)", "ACCEPTED", "eval 0-4"),
    ("refit_hyb", "v3", "hyb_refit_lean", 10_000, "hybrid + chain-scoped questions (H_mycelic_lean)", "Pareto arm", "eval 0-4"),
    ("v50_v3", "H_old", "H_v3_qf0.65", 50_000, "ranker v3 + qf 0.65 + batched, 50k", "ACCEPTED", "eval 0-2"),
    ("v50_v3", "H_v3_qf0.65", "H_v3_qf0.80", 50_000, "question_frac 0.80 at 50k", "rejected", "eval 0-2"),
    ("v50_v3", "H_v3_qf0.65", "H_v3_qf0.65_rx", 50_000, "local re-extraction at 50k", "rejected", "eval 0-1"),
    ("v50_hyb", "v3", "hyb", 50_000, "hybrid link timing at 50k (+ refitted ranker)", "validation", "eval 0-2"),
    ("v50_hyb", "v3", "hyb_lean", 50_000, "hybrid + chain-scoped questions at 50k", "Pareto arm", "eval 0-2"),
    ("prev_batched", "prev", "prev_batched", 10_000, "v1 hierarchy re-metered with batched descent (identical decisions)", "metering control", "eval 0-4"),
    ("prev_batched50", "prev", "prev_batched", 50_000, "v1 hierarchy re-metered with batched descent at 50k", "metering control", "eval 0-2"),
    ("diag_reg", None, None, 10_000, "unbounded register (DIAGNOSTIC ONLY, not a fix)", "diagnostic", "eval 0-4"),
    ("diag_both", None, None, 10_000, "unbounded register + full question budget (DIAGNOSTIC)", "diagnostic", "eval 0-4"),
    ("cal_screen", "base", "span2", 10_000, "chain-scoped questions alone (calibration seeds)", "screen", "cal 500-502"),
    ("stale_cal", "hyb", "hyb_strong", 10_000, "cluster-aware staleness gate (strong positives only), calibration seeds", "screen", "cal 500-502"),
    ("strong_fresh", "hyb", "strong_hybrk", 10_000, "strong staleness gate, frozen ranker, FRESH panel", "fresh-panel read", "eval 10-14"),
    ("strong_fresh", "hyb", "strong_refit", 10_000, "strong staleness gate + refitted ranker, FRESH panel", "fresh-panel read", "eval 10-14"),
]


def ledger_table() -> str:
    lines = ["| change | scale | seeds | Δ found | Δ rare | Δ cov. | Δ decoy all | Δ D1 | Δ D2 | Δ D3 | Δ D5 stale | compute | calls (metering) | status |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for tag, base, variant, scale, what, status, seeds in LEDGER:
        a, b = _arms(tag, base, variant)
        common = sorted(set(a) & set(b))
        if not common:
            lines.append(f"| {what} | {scale:,} | {seeds} | — | — | — | — | — | — | — | — | — | — | {status} (no rows) |")
            continue
        pr = paired_rows(a, b, ["found_anywhere_in_register", "rare_signal_recall",
                                "evidence_coverage_2links", "decoy_acceptance_all",
                                "compute_units", "inference_calls"] + DECOYS)
        f = pr["found_anywhere_in_register"]
        k, n, _ = sign_test(f[2].tolist())
        cu = pr["compute_units"]
        ca = pr["inference_calls"]
        lines.append(
            f"| {what} | {scale:,} | {seeds} | {f[2].mean():+.3f} ({k}/{n} better) | "
            f"{pr['rare_signal_recall'][2].mean():+.3f} | {pr['evidence_coverage_2links'][2].mean():+.3f} | "
            f"{pr['decoy_acceptance_all'][2].mean():+.3f} | "
            + " | ".join(f"{pr[d][2].mean():+.3f}" for d in DECOYS) + " | "
            f"{(cu[1] / cu[0] - 1) * 100:+.0f}% | "
            f"{(ca[1] / ca[0] - 1) * 100:+.0f}% | {status} |")
    return "\n".join(lines)


def sweep_table(tag: str, base_label: str) -> str:
    """A multi-arm paired file as one row per arm (mean over its seeds, with
    the paired delta against the base arm), used for the budget sweeps."""
    rows = _quick(tag)
    if not rows:
        return "_(not run)_"
    by: Dict[str, Dict[int, Dict]] = {}
    for r in rows:
        by.setdefault(r["arch"], {})[r["seed"]] = r
    base = by.get(base_label, {})
    seeds = sorted({r["seed"] for r in rows})
    scale = sorted({r["scale"] for r in rows})
    lines = [f"seeds {seeds[0]}–{seeds[-1]} at {', '.join(f'{s:,}' for s in scale)} "
             f"({'calibration' if seeds[0] >= 500 else 'evaluation'} panel), paired Δ against {base_label}:", "",
             "| arm | seeds | found | Δ found | rare recall | evidence cov. | AP | decoy acc. | compute | calls |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label, d in by.items():
        s = sorted(d)
        m = lambda k: float(np.mean([d[x][k] for x in s]))
        common = sorted(set(s) & set(base))
        dl = float(np.mean([d[x]["found_anywhere_in_register"] - base[x]["found_anywhere_in_register"]
                            for x in common])) if common and label != base_label else 0.0
        lines.append(f"| {label} | {len(s)} | {m('found_anywhere_in_register'):.3f} | {dl:+.3f} | "
                     f"{m('rare_signal_recall'):.3f} | {m('evidence_coverage_2links'):.3f} | "
                     f"{m('average_precision'):.3f} | {m('decoy_acceptance_all'):.3f} | "
                     f"{m('compute_units'):.2e} | {m('inference_calls'):.2e} |")
    return "\n".join(lines)


def fresh_panel_numbers(tag: str = "strong_fresh", arm: str = "hyb") -> Dict[str, float]:
    """The frozen configuration's one read on seeds 10-14 (the base arm of the
    staleness-gate test), as levels."""
    rows = [r for r in _quick(tag) if r["arch"] == arm]
    if not rows:
        return {}
    m = lambda k: float(np.mean([r[k] for r in rows]))
    return {"found": m("found_anywhere_in_register"), "rare": m("rare_signal_recall"),
            "cov": m("evidence_coverage_2links"), "decoy": m("decoy_acceptance_all"),
            "d5": m("decoy_D5_stale_chain"), "cu": m("compute_units"), "n": len(rows)}


def compute_per_accepted_change() -> str:
    """Compute cost per accepted change at 10k, in the order they were stacked."""
    steps = [
        ("v1 hierarchy (hand ranker, qf 0.25), metered unbatched as benchmarked", "v3_H", None, 0, False),
        ("v1 hierarchy re-metered with batched descent (identical decisions) — the like-for-like base", "prev_batched", "prev_batched", None, True),
        ("+ ranker v3, qf 0.65, batched descent", "v3_H", None, 1, True),
        ("+ hybrid link timing (refitted ranker)", "refit_hyb", "hyb_refit", None, True),
    ]
    lines = ["| configuration | found | rare recall | compute | calls (metering) | kernel prompt tokens | Δ found per +10% compute (vs previous row) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    prev = None
    for name, tag, label, parity, chain in steps:
        rows = _quick(tag)
        if label is None:
            sel = [r for i, r in enumerate(rows) if i % 2 == parity]
        else:
            sel = [r for r in rows if r["arch"] == label]
        if not sel:
            lines.append(f"| {name} | — | — | — | — | — | — |")
            continue
        m = lambda k: float(np.mean([r[k] for r in sel]))
        eff = "—"
        if chain and prev is not None and m("compute_units") > prev[1] * 1.02:
            eff = f"{(m('found_anywhere_in_register') - prev[0]) / ((m('compute_units') / prev[1] - 1) * 10):+.3f}"
        elif chain and prev is not None:
            eff = f"{m('found_anywhere_in_register') - prev[0]:+.3f} at {(m('compute_units') / prev[1] - 1) * 100:+.1f}% compute"
        lines.append(f"| {name} | {m('found_anywhere_in_register'):.3f} | {m('rare_signal_recall'):.3f} | "
                     f"{m('compute_units'):.2e} | {m('inference_calls'):.2e} | {m('max_context_tokens'):.2e} | {eff} |")
        prev = (m("found_anywhere_in_register"), m("compute_units"))
    lines.append("")
    lines.append("The first row is the v1 base as it was benchmarked (unbatched metering); the second is the same "
                 "decisions re-metered with batched descent, which is the base every later row should be read "
                 "against. Batching is a metering convention: it changes no decision and no per-record token.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# funnels, old and new
# ---------------------------------------------------------------------------

def _funnel(base: str) -> List[Dict]:
    return _rows("loss_funnel.jsonl", base)


def funnel_compare(scale: int, arch: str = "H_mycelic_full") -> str:
    from .loss_report import STAGE_LABEL, stages_for
    old = [r for r in _funnel(V1) if not r.get("summary") and r["scale"] == scale and r["arch"] == arch]
    new = [r for r in _funnel(ART) if not r.get("summary") and r["scale"] == scale and r["arch"] == arch]
    if not old or not new:
        return "_(funnel rows missing for one side)_"
    stages = stages_for(arch, new)

    def surv(rows, s, rare=None):
        sub = [r for r in rows if r.get(s) is not None and (rare is None or r["rare"] == rare)]
        return sum(1 for r in sub if r[s]) / len(sub) if sub else float("nan")

    lines = ["| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for s in stages:
        lines.append(f"| {STAGE_LABEL[s]} | {surv(old, s):.3f} | {surv(new, s):.3f} | "
                     f"{surv(old, s, True):.3f} | {surv(new, s, True):.3f} | "
                     f"{surv(old, s, False):.3f} | {surv(new, s, False):.3f} |")
    lines.append(f"| patterns | {len(old)} | {len(new)} | {sum(r['rare'] for r in old)} | "
                 f"{sum(r['rare'] for r in new)} | {sum(not r['rare'] for r in old)} | "
                 f"{sum(not r['rare'] for r in new)} |")
    return "\n".join(lines)


def terminal_loss_compare(scale: int, arch: str = "H_mycelic_full") -> str:
    from .loss_report import LOSS_LABEL, STAGE_LABEL, stages_for
    old = [r for r in _funnel(V1) if not r.get("summary") and r["scale"] == scale and r["arch"] == arch]
    new = [r for r in _funnel(ART) if not r.get("summary") and r["scale"] == scale and r["arch"] == arch]
    if not old or not new:
        return "_(funnel rows missing for one side)_"
    stages = stages_for(arch, new) + ["reported"]
    lines = ["| the pattern died at | OLD | NEW | OLD rare | NEW rare |", "|---|---:|---:|---:|---:|"]
    for s in stages:
        key = "" if s == "reported" else s
        lab = LOSS_LABEL.get(s, STAGE_LABEL.get(s, "**reported**"))
        oc = sum(1 for r in old if r["loss_stage"] == key)
        nc = sum(1 for r in new if r["loss_stage"] == key)
        orc = sum(1 for r in old if r["loss_stage"] == key and r["rare"])
        nrc = sum(1 for r in new if r["loss_stage"] == key and r["rare"])
        lines.append(f"| {lab} | {oc} ({oc / len(old):.0%}) | {nc} ({nc / len(new):.0%}) | {orc} | {nrc} |")
    return "\n".join(lines)


def largest_remaining_loss(scale: int, arch: str = "H_mycelic_full") -> Tuple[str, int, int]:
    """(stage label, count, total) of the most common terminal loss in the NEW funnel."""
    from .loss_report import LOSS_LABEL, STAGE_LABEL
    new = [r for r in _funnel(ART) if not r.get("summary") and r["scale"] == scale and r["arch"] == arch]
    if not new:
        return ("—", 0, 0)
    counts: Dict[str, int] = {}
    for r in new:
        if r["loss_stage"]:
            counts[r["loss_stage"]] = counts.get(r["loss_stage"], 0) + 1
    if not counts:
        return ("none", 0, len(new))
    s = max(counts, key=counts.get)
    return (LOSS_LABEL.get(s, STAGE_LABEL.get(s, s)), counts[s], len(new))


# ---------------------------------------------------------------------------
# ranker evaluation and the frozen configuration
# ---------------------------------------------------------------------------

def ranker_eval_table() -> str:
    p = os.path.join(ART, "ranker_eval_final.json")
    if not os.path.exists(p):
        return "_(ranker_eval_final.json missing: run final_rerun.sh)_"
    ev = json.load(open(p))
    lines = ["| dump | system | gold patterns | matchable | found, hand ranker | found, learned (same count) | AUC hand | AUC learned |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for tag, r in ev.items():
        for arch, pa in r.get("per_arch", {}).items():
            lines.append(f"| {tag} | {arch} | {pa['gold_patterns']} | {pa['matchable_patterns']} | "
                         f"{pa['found_baseline']:.3f} | {pa['found_learned_rank_only']:.3f} | "
                         f"{pa['auc_conf']:.3f} | {pa['auc_learned']:.3f} |")
    lines.append("")
    lines.append("Evaluation rows are the held-out seeds 0–4 only; the logistic is refitted "
                 "on the calibration seeds of the same dump with the stored l2 and "
                 "interaction choice, so the AUC and found columns are out-of-sample.")
    return "\n".join(lines)


def frozen_config_table() -> str:
    cal = json.load(open(os.path.join(ART, "calibration.json")))
    prov = cal.get("vnext", {})
    r = cal.get("ranker", {})
    lines = ["| knob | frozen value | seeds the cited evidence was read on | evidence |", "|---|---|---|---|"]
    for k, v in prov.items():
        # the "chosen on" column is read from the evidence files themselves:
        # a knob whose only evidence is on the evaluation panel says so
        where = []
        for ev in [e.strip() for e in str(v.get("evidence", "")).split(",") if e.strip()]:
            rows = _rows(ev)
            if not rows:
                where.append(f"{ev}: (file missing)")
                continue
            sd = sorted({r["seed"] for r in rows})
            sc = sorted({r["scale"] for r in rows})
            panel = ("calibration" if all(x >= 500 for x in sd)
                     else "evaluation (development panel)" if all(x < 5 for x in sd)
                     else "mixed")
            where.append(f"{ev}: seeds {sd[0]}–{sd[-1]} at {', '.join(f'{x:,}' for x in sc)} — {panel}")
        lines.append(f"| `{k}` | `{v['value']}` | {'; '.join(where) or '—'} | "
                     f"`{v.get('evidence', '')}` |")
    if r:
        lines.append(f"| ranker | {r.get('kind', 'logistic')}, l2 {r.get('l2')}, "
                     f"interactions {r.get('interactions')}, {r.get('n_train')} candidates | "
                     f"seeds {r.get('seeds')} | `{r.get('source_file', 'calibration.json')}` |")
    sel = cal.get("ranker_archs")
    if sel:
        lines.append(f"| ranker adopted by | {', '.join(sel)} | calibration seeds | ranker_arch_table |")
    return "\n".join(lines)


def headline_numbers() -> Dict[str, float]:
    """The handful of numbers the executive text quotes."""
    out: Dict[str, float] = {}
    old = e1_rows(V1)
    new = e1_rows(ART)
    for tag, f in (("prevb", "prev_batched"), ("prevb50", "prev_batched50")):
        rows = [r for r in _quick(f) if r["arch"] == "prev_batched"]
        sc = 50_000 if tag.endswith("50") else 10_000
        for k in CORE:
            out[f"{tag}_{k}_{sc}"] = float(np.mean([r[k] for r in rows])) if rows else float("nan")
    conf = range(5, 10)
    for tag, rows, arch in (("old_conf", old, "H_mycelic_full"), ("new_conf", new, "H_mycelic_full"),
                            ("a2_conf", new, "A2_chunked_ctx"), ("a2_old_conf", old, "A2_chunked_ctx")):
        d = {s: r for s, r in by_seed(rows, arch, 10_000).items() if s in conf}
        for k in CORE + ["decoy_acceptance_all"]:
            out[f"{tag}_{k}_10000"] = float(np.mean([r[k] for r in d.values()])) if d else float("nan")
        out[f"{tag}_n_10000"] = len(d)
    for scale in (10_000, 50_000):
        for tag, rows, arch in (("old", old, "H_mycelic_full"), ("new", new, "H_mycelic_full"),
                                ("a2", new, "A2_chunked_ctx"), ("a2_old", old, "A2_chunked_ctx"),
                                ("lean", new, "H_mycelic_lean"), ("prev", new, "H_mycelic_prev")):
            d = {s: r for s, r in by_seed(rows, arch, scale).items() if s < 5}
            for k in CORE + ["decoy_acceptance_all", "common_signal_recall"]:
                out[f"{tag}_{k}_{scale}"] = float(np.mean([r[k] for r in d.values()])) if d else float("nan")
            out[f"{tag}_n_{scale}"] = len(d)
    return out


if __name__ == "__main__":
    for sc in (10_000, 50_000):
        print(f"\n## OLD vs NEW at {sc:,}\n")
        print(old_new_table(sc))
        print(f"\n## gap at {sc:,}\n")
        print(gap_table(sc))
        print(f"\n## v1 reproduction at {sc:,}\n")
        print(prev_reproduction_table(sc))
        print(f"\n## funnel {sc:,}\n")
        print(funnel_compare(sc))
        print(terminal_loss_compare(sc))
    print("\n## ledger\n")
    print(ledger_table())
    print("\n## compute per accepted change\n")
    print(compute_per_accepted_change())
    print("\n## ranker\n")
    print(ranker_eval_table())
    print("\n## frozen\n")
    print(frozen_config_table())

"""The vNext deliverables asked for by the research pack, generated from
artifacts so that every number is traceable.

    python3 -m research.mycelic.vnext_docs

writes docs/mycelic_vnext/{NEXT_RESEARCH_REPORT, VNEXT_ARCHITECTURE,
EXPERIMENT_MATRIX, LOSS_ACCOUNTING, REVIEWER_ATTACK}.md and the two SVG
diagrams they embed.  Prose is fixed; tables and the quoted numbers come
from vnext_data (artifacts/, artifacts/v1/, artifacts/quick_*.jsonl,
loss_funnel.jsonl, ranker_eval_final.json, calibration.json).
"""
from __future__ import annotations

import json
import math
import os
import time

from . import vnext_data as D
from .runner import ART

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(ROOT, "docs", "mycelic_vnext")


def _n(x: float, nd: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def _pct(a: float, b: float) -> str:
    if not a or math.isnan(a) or math.isnan(b):
        return "n/a"
    return f"{(b / a - 1) * 100:+.0f}%"


def _mean(rows, k: str) -> float:
    rows = list(rows)
    return sum(r[k] for r in rows) / len(rows) if rows else float("nan")


def _span(seeds) -> str:
    """'0–4' for a contiguous seed set, else the list."""
    s = sorted(seeds)
    if not s:
        return "none"
    return f"{s[0]}–{s[-1]}" if s == list(range(s[0], s[-1] + 1)) else ", ".join(map(str, s))


def _e1_seeds(scale: int) -> str:
    return _span(D.by_seed(D.e1_rows(), "H_mycelic_full", scale))


def _quick_seeds(tag: str) -> str:
    return _span({r["seed"] for r in D._quick(tag)})


def _funnel_rows(scale: int, base: str = ART):
    return [r for r in D._funnel(base) if not r.get("summary") and r["scale"] == scale
            and r["arch"] == "H_mycelic_full"]


def _funnel_seeds(scale: int) -> str:
    new = {r["seed"] for r in _funnel_rows(scale)}
    old = {r["seed"] for r in _funnel_rows(scale, D.V1)}
    lab = f"seeds {_span(new)}"
    return lab if not old or old == new else f"{lab}; OLD seeds {_span(old)}"


def _loss50() -> dict:
    """Where the hierarchy's 50k gold patterns die in the NEW funnel (terminal loss counts)."""
    rows = _funnel_rows(50_000)

    def term(stage, rare=None):
        return sum(1 for r in rows if r["loss_stage"] == stage and (rare is None or r["rare"] == rare))
    inv = [r for r in rows if r.get("sketch_visible") is False]
    return {"n": len(rows), "seeds50": _span({r["seed"] for r in rows}),
            "sk": term("sketch_visible"), "sk_rare": term("sketch_visible", True),
            "qb": term("questioned"), "qb_rare": term("questioned", True),
            "ex": term("extracted"), "dr": term("descent_reached"),
            "inv": len(inv), "span": sum(1 for r in inv if r.get("sketch_fail") == "span_lt2")}


def _ctx_range(scale: int) -> str:
    """Per-seed range of the hierarchy's kernel prompt (max_context_tokens), in M tokens."""
    v = [r["max_context_tokens"] / 1e6 for r in D.by_seed(D.e1_rows(), "H_mycelic_full", scale).values()]
    return f"{min(v):.1f}–{max(v):.1f}M" if v else "n/a"


def _call_drop(scale: int) -> float:
    """Model-call reduction of the NEW hierarchy against v1 (percent, positive = fewer calls)."""
    old = D.by_seed(D.e1_rows(D.V1), "H_mycelic_full", scale)
    new = D.by_seed(D.e1_rows(), "H_mycelic_full", scale)
    common = sorted(set(old) & set(new))
    if not common:
        return float("nan")
    return (1 - _mean((new[s] for s in common), "inference_calls")
            / _mean((old[s] for s in common), "inference_calls")) * 100


# ---------------------------------------------------------------------------
# diagrams
# ---------------------------------------------------------------------------

TOPOLOGY_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560" font-family="Helvetica, Arial, sans-serif" font-size="13">
<defs><marker id="a" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#333"/></marker>
<marker id="b" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#b5451b"/></marker></defs>
<rect x="0" y="0" width="900" height="560" fill="#fff"/>
<text x="450" y="24" text-anchor="middle" font-size="16" font-weight="bold">Mycelic vNext: what moves up, what moves down, and where the model calls are</text>
<!-- levels -->
<g>
<rect x="300" y="46" width="300" height="74" rx="8" fill="#e8eef7" stroke="#2b4c7e" stroke-width="1.5"/>
<text x="450" y="66" text-anchor="middle" font-weight="bold">ENTERPRISE KERNEL (frontier tier)</text>
<text x="450" y="84" text-anchor="middle">sketch triage · question policy · candidate synthesis</text>
<text x="450" y="101" text-anchor="middle">hybrid temporal DP · learned ranker · fixed-size risk register</text>
<rect x="60" y="160" width="220" height="50" rx="8" fill="#f3f3f3" stroke="#666"/><text x="170" y="181" text-anchor="middle" font-weight="bold">REGION nodes</text><text x="170" y="198" text-anchor="middle">merge site sketches, route down</text>
<rect x="340" y="160" width="220" height="50" rx="8" fill="#f3f3f3" stroke="#666"/><text x="450" y="181" text-anchor="middle" font-weight="bold">SITE nodes</text><text x="450" y="198" text-anchor="middle">build the (entity, predicate) sketch</text>
<rect x="620" y="160" width="220" height="50" rx="8" fill="#f3f3f3" stroke="#666"/><text x="730" y="181" text-anchor="middle" font-weight="bold">DEPT / TEAM nodes</text><text x="730" y="198" text-anchor="middle">hold knowledge objects, index anchors</text>
<rect x="150" y="280" width="600" height="60" rx="8" fill="#fbf1e6" stroke="#b5451b" stroke-width="1.5"/>
<text x="450" y="302" text-anchor="middle" font-weight="bold">USER nodes (sovereign memory; raw records never leave)</text>
<text x="450" y="322" text-anchor="middle">local extraction → claims (knowledge objects) with lineage; answer targeted reads from their own index</text>
</g>
<!-- upward flow -->
<g stroke="#333" stroke-width="1.5" fill="none" marker-end="url(#a)">
<path d="M 300 280 L 170 212"/><path d="M 450 280 L 450 212"/><path d="M 600 280 L 730 212"/>
<path d="M 170 160 L 330 122"/><path d="M 450 160 L 450 122"/><path d="M 730 160 L 570 122"/>
</g>
<text x="300" y="262" fill="#333">claims up (no raw text)</text>
<text x="560" y="145" fill="#333">sketch bits + counts up</text>
<!-- downward flow -->
<g stroke="#b5451b" stroke-width="1.5" fill="none" stroke-dasharray="6 4" marker-end="url(#b)">
<path d="M 600 100 C 760 120, 800 200, 760 280"/>
<path d="M 300 100 C 140 120, 100 200, 140 280"/>
</g>
<text x="640" y="232" fill="#b5451b">targeted questions down:</text>
<text x="640" y="246" fill="#b5451b">(entity, predicates, fan-out)</text>
<text x="20" y="232" fill="#b5451b">routed by sketch + index;</text>
<text x="20" y="246" fill="#b5451b">metered per node, not per record</text>
<!-- register -->
<rect x="640" y="46" width="230" height="74" rx="8" fill="#fff" stroke="#2b4c7e" stroke-dasharray="4 3"/>
<text x="755" y="70" text-anchor="middle" font-weight="bold">risk register (fixed cap)</text>
<text x="755" y="88" text-anchor="middle">min(6000, max(600, n_entities))</text>
<text x="755" y="106" text-anchor="middle">ranked by the learned score</text>
<!-- privacy boundary -->
<line x1="40" y1="265" x2="860" y2="265" stroke="#b5451b" stroke-width="1" stroke-dasharray="2 4"/>
<text x="860" y="262" text-anchor="end" fill="#b5451b" font-size="11">privacy boundary: raw text stays below this line</text>
<!-- legend -->
<g transform="translate(60,380)">
<text x="0" y="0" font-weight="bold">Per round, the kernel</text>
<text x="0" y="20">1. reads the merged sketch and ranks entities by foreign-site causal span (no model call);</text>
<text x="0" y="40">2. spends the question budget (max(220, 0.65 × queue)) on the top entities, one routed descent each;</text>
<text x="0" y="60">3. receives claims, forms one candidate per (entity, chain) with the hybrid temporal DP;</text>
<text x="0" y="80">4. scores candidates with the learned ranker (kernel-side features only) and keeps the register cap;</text>
<text x="0" y="100">5. asks follow-up questions for missing links / weak support / contradictions, then repeats.</text>
<text x="0" y="135" font-weight="bold">Model calls</text>
<text x="0" y="155">routing and user reads are metered once per node per round (batched); the kernel re-reads its pool once per round.</text>
</g>
</svg>
"""

LOOP_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="900" height="300" viewBox="0 0 900 300" font-family="Helvetica, Arial, sans-serif" font-size="12">
<defs><marker id="c" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#333"/></marker></defs>
<rect x="0" y="0" width="900" height="300" fill="#fff"/>
<text x="450" y="22" text-anchor="middle" font-size="15" font-weight="bold">The discovery loop as the loss funnel sees it (stage names = loss_funnel.jsonl columns)</text>
<g font-size="12">
<rect x="20" y="60" width="120" height="56" rx="6" fill="#f3f3f3" stroke="#666"/><text x="80" y="82" text-anchor="middle" font-weight="bold">extracted</text><text x="80" y="100" text-anchor="middle">local claims</text>
<rect x="165" y="60" width="120" height="56" rx="6" fill="#f3f3f3" stroke="#666"/><text x="225" y="82" text-anchor="middle" font-weight="bold">sketch_visible</text><text x="225" y="100" text-anchor="middle">≥2 foreign sites</text>
<rect x="310" y="60" width="120" height="56" rx="6" fill="#f3f3f3" stroke="#666"/><text x="370" y="82" text-anchor="middle" font-weight="bold">questioned</text><text x="370" y="100" text-anchor="middle">inside budget</text>
<rect x="455" y="60" width="120" height="56" rx="6" fill="#f3f3f3" stroke="#666"/><text x="515" y="82" text-anchor="middle" font-weight="bold">in_pool</text><text x="515" y="100" text-anchor="middle">≥2 links at kernel</text>
<rect x="600" y="60" width="120" height="56" rx="6" fill="#f3f3f3" stroke="#666"/><text x="660" y="82" text-anchor="middle" font-weight="bold">matched_tau</text><text x="660" y="100" text-anchor="middle">candidate ≥ 0.5</text>
<rect x="745" y="60" width="130" height="56" rx="6" fill="#e8eef7" stroke="#2b4c7e" stroke-width="1.5"/><text x="810" y="82" text-anchor="middle" font-weight="bold">in_register</text><text x="810" y="100" text-anchor="middle">= reported</text>
</g>
<g stroke="#333" stroke-width="1.5" fill="none" marker-end="url(#c)">
<path d="M140 88 L163 88"/><path d="M285 88 L308 88"/><path d="M430 88 L453 88"/><path d="M575 88 L598 88"/><path d="M720 88 L743 88"/>
</g>
<g font-size="11" fill="#b5451b">
<text x="370" y="140" text-anchor="middle">v1: largest first loss</text>
<text x="370" y="154" text-anchor="middle">(budget 0.25 × queue)</text>
<text x="515" y="140" text-anchor="middle">coverage; 50k binding</text>
<text x="515" y="154" text-anchor="middle">(lost mostly at the sketch)</text>
<text x="660" y="140" text-anchor="middle">link timing (hybrid DP)</text>
<text x="810" y="140" text-anchor="middle">v1: ordering under the cap;</text>
<text x="810" y="154" text-anchor="middle">learned ranker</text>
</g>
<g font-size="12">
<text x="20" y="200" font-weight="bold">vNext changes, by stage:</text>
<text x="20" y="220">questioned — budget 0.25 → 0.65 of the triage queue (chosen on development seeds 0–4); metering batched per node so calls fall while decisions are identical.</text>
<text x="20" y="240">matched — a link is dated at its heaviest witness cluster, or floats across clusters in the DP when its witnesses disagree (hybrid), instead of its earliest mention.</text>
<text x="20" y="260">in_register — the hand-set logistic decides HOW MANY candidates pass; a ranker fitted on calibration seeds over kernel-side features decides WHICH.</text>
<text x="20" y="280">untouched — gold labels, evaluation worlds, register cap, metrics, sketch support threshold.</text>
</g>
</svg>
"""


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def _hn():
    return D.headline_numbers()


def next_research_report() -> str:
    h = _hn()
    l10 = D.largest_remaining_loss(10_000)
    l50 = D.largest_remaining_loss(50_000)
    gap10 = h["a2_found_anywhere_in_register_10000"] - h["new_found_anywhere_in_register_10000"]
    gap10c = h["a2_conf_found_anywhere_in_register_10000"] - h["new_conf_found_anywhere_in_register_10000"]
    gap50 = h["a2_found_anywhere_in_register_50000"] - h["new_found_anywhere_in_register_50000"]
    t = []
    t.append("# Mycelic vNext — research report\n")
    t.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} from "
             f"`research/mycelic/artifacts/` (new) and `artifacts/v1/` (old) by "
             f"`vnext_docs.py`. Every table is computed from those files._\n")
    t.append("## 1. Result\n")
    t.append("The brief asked for the discovery accuracy of the Mycelic hierarchy to be raised as fast "
             "and as compute-efficiently as possible without touching the metrics, the gold labels, the "
             "evaluation worlds, the register cap or the calibration/evaluation seed split, and then for "
             "the whole benchmark to be rerun on the frozen configuration. This is the paired result on "
             "identical worlds (OLD = the archived v1 rows, NEW = the rerun).\n")
    t.append("### 1.1 OLD vs NEW at 10,000 people — development panel (seeds 0–4)\n")
    t.append("These five worlds were read after every accepted and rejected change during the work "
             "(about eleven decisions); their numbers are development-panel numbers and carry a "
             "selection optimism of roughly +0.02 to +0.04 found on the small deltas (section 6).\n")
    t.append(D.old_new_table(10_000, seeds=range(0, 5)))
    t.append("\n### 1.2 OLD vs NEW at 10,000 people — confirmation panel (seeds 5–9, never read during development)\n")
    t.append("No paired experiment, dump, funnel or decision touched these five worlds before the "
             "final rerun; this is the number to quote.\n")
    t.append(D.old_new_table(10_000, seeds=range(5, 10)))
    t.append("\n### 1.3 OLD vs NEW at 50,000 people (seeds 0–4; seeds 0–2 were the development panel at this scale)\n")
    t.append(D.old_new_table(50_000))
    t.append("\n### 1.4 In one paragraph\n")
    t.append(f"On the confirmation panel (10k, seeds 5–9) the hierarchy's found rate goes from "
             f"{_n(h['old_conf_found_anywhere_in_register_10000'])} to {_n(h['new_conf_found_anywhere_in_register_10000'])} "
             f"(rare recall {_n(h['old_conf_rare_signal_recall_10000'])} → {_n(h['new_conf_rare_signal_recall_10000'])}, "
             f"A2 {_n(h['a2_old_conf_found_anywhere_in_register_10000'])} → {_n(h['a2_conf_found_anywhere_in_register_10000'])}). "
             f"On the development panel (seeds 0–4) it goes from {_n(h['old_found_anywhere_in_register_10000'])} to "
             f"{_n(h['new_found_anywhere_in_register_10000'])} (evidence coverage "
             f"{_n(h['old_evidence_coverage_2links_10000'])} → {_n(h['new_evidence_coverage_2links_10000'])}, "
             f"rare recall {_n(h['old_rare_signal_recall_10000'])} → {_n(h['new_rare_signal_recall_10000'])}). Compute: "
             f"{_pct(h['old_compute_units_10000'], h['new_compute_units_10000'])} against the v1 base as it was "
             f"benchmarked (unbatched metering) and {_pct(h['prevb_compute_units_10000'], h['new_compute_units_10000'])} "
             f"against the same v1 decisions re-metered with batched descent, which is the like-for-like figure; the "
             f"model-call reduction ({_pct(h['old_inference_calls_10000'], h['new_inference_calls_10000'])}) is a "
             f"metering convention, not fewer decisions. At 50k it goes from "
             f"{_n(h['old_found_anywhere_in_register_50000'])} to {_n(h['new_found_anywhere_in_register_50000'])} "
             f"(coverage {_n(h['old_evidence_coverage_2links_50000'])} → {_n(h['new_evidence_coverage_2links_50000'])}) for "
             f"{_pct(h['old_compute_units_50000'], h['new_compute_units_50000'])} compute as benchmarked and "
             f"{_pct(h['prevb50_compute_units_50000'], h['new_compute_units_50000'])} like-for-like. "
             f"The centralised chunked-context control A2 also improved, because it adopts the same learned ranker "
             f"(its calibration said the ranker was not worse for it): {_n(h['a2_old_found_anywhere_in_register_10000'])} → "
             f"{_n(h['a2_found_anywhere_in_register_10000'])} at 10k. The remaining gap to A2 is "
             f"{gap10:+.3f} found at 10k on the development panel ({gap10c:+.3f} on the confirmation panel) "
             f"and {gap50:+.3f} at 50k, at "
             f"{h['new_compute_units_10000'] / max(1e-9, h['a2_compute_units_10000']):.2f}× (development panel) and "
             f"{h['new_compute_units_50000'] / max(1e-9, h['a2_compute_units_50000']):.2f}× of A2's compute respectively. "
             f"Decoy acceptance rose with the ranker at 10k "
             f"({_n(h['old_decoy_acceptance_all_10000'])} → {_n(h['new_decoy_acceptance_all_10000'])}), and the stale-chain "
             f"family D5 in particular ({_n(h['old_decoy_D5_stale_chain_10000'])} → {_n(h['new_decoy_D5_stale_chain_10000'])}); "
             f"both are reported as regressions, not hidden. The one attempt to train the ranker away from decoys lost found "
             f"and rare recall on the held-out seeds and was rejected; the D5 mechanism (a staleness gate one late routine "
             f"mention defeats) is understood and its fix is queued. The frozen configuration also requires a kernel prompt "
             f"of {h['new_max_context_tokens_10000'] / 1e6:.1f}M tokens at 10k and {h['new_max_context_tokens_50000'] / 1e6:.1f}M "
             f"at 50k, above the modelled tier's 1M context (section 5b).\n")
    f10 = h["new_found_anywhere_in_register_10000"]
    f10c = h["new_conf_found_anywhere_in_register_10000"]
    f50 = h["new_found_anywhere_in_register_50000"]
    met10 = "met" if f10c >= 0.70 else "not met"
    met50 = "met" if f50 >= 0.60 else "not met"
    t.append("Targets from the brief: 10k found ≥ 0.70 (stretch 0.75), 50k ≥ 0.60 (stretch 0.70 while materially below "
             f"A2 compute). Reached: 10k {_n(f10c)} on the confirmation panel ({_n(f10)} on the development panel; "
             f"{met10}), 50k {_n(f50)} ({met50}), the 50k figure at "
             f"{h['new_compute_units_50000'] / max(1e-9, h['a2_compute_units_50000']):.2f}× of A2's compute. "
             "Section 5 says which stage holds the rest.\n")
    t.append("## 2. Against the centralised controls on the same NEW worlds\n")
    t.append(f"### 2.1 10,000 (seeds {_e1_seeds(10_000)})\n")
    t.append(D.gap_table(10_000))
    t.append(f"\n### 2.2 50,000 (seeds {_e1_seeds(50_000)})\n")
    t.append(D.gap_table(50_000))
    t.append("\n`H_mycelic_prev` is the v1 hierarchy (question budget 0.25, unbatched metering, earliest-mention link "
             "timing, hand-set ranker) run inside the new suite; it is there to show the old configuration reproduces "
             "on the new worlds. `H_mycelic_lean` is the compute-lean Pareto point (chain-scoped triage questions; "
             "about half the compute, less evidence coverage).\n")
    t.append("### 2.3 Does the v1 configuration reproduce inside the new suite?\n")
    t.append(D.prev_reproduction_table(10_000))
    t.append("\nA non-zero difference here would mean a code change altered v1 behaviour; the intended reading is "
             "\"the same to the third decimal\" for discovery and \"identical\" for compute.\n")
    t.append("## 3. What changed, and the mechanism behind each change\n")
    t.append("The loss accounting (`docs/MYCELIC_LOSS_ACCOUNTING.md`) showed the 35-point gap was not where the handoff "
             "pack expected. The sketch saw essentially every gold pattern; retrieval reached most of them once asked; "
             "the two stages that ate the gold were the question budget (calibrated to 0.25 of the triage queue "
             "because the hand-set confidence let extra candidates flood the register) and the register cut itself "
             "(candidates matched at ≥ 0.5 but out-ranked by same-entity siblings and background chains). Three "
             "changes address exactly those stages; nothing else was changed in the frozen configuration.\n")
    t.append("1. **Learned ranker over kernel-side features** (`calibrator.py`). The hand-set logistic still decides "
             "how many candidates pass the 0.5 gate; a class-weighted logistic fitted on the calibration seeds "
             "(500–502) over 45 features the kernel already holds — per-link support and independence, lineage "
             "dispersion, lag statistics, the anchor's triage context, evidence shape (origin users, single-witness "
             "fraction, echo ratio) and the kernel's own attribution verdict — decides which candidates fill that "
             "count. No raw text, no per-user record, no evaluation seed is involved. Adoption is decided per "
             "architecture on the calibration seeds so no control is compared at a ranker chosen for somebody else.\n")
    t.append("2. **Question budget 0.65 of the triage queue, with batched metering** (`systems.py`). With ordering "
             "fixed, the budget could be widened; the paired sweep on the development panel (0.50/0.65/0.80/1.00 "
             f"at 10k on seeds {_quick_seeds('qf_v3')}; 0.65/0.80/1.00 at 50k on seeds {_quick_seeds('v50_v3')}) "
             "saturates at 0.65 at both "
             "scales (coverage reaches 0.97 at 10k; 0.80 and 1.00 buy nothing and lose AP); on the calibration seeds "
             "found is flat from 0.50 to 0.80 (section 6). Batching meters one "
             "routing call per node and one read per queried user per round, with identical decisions and "
             "per-record tokens, which is why calls fall by about two thirds while compute rises.\n")
    t.append("3. **Hybrid link timing in the temporal DP** (`ops.py`). A link was dated at the earliest mention of its "
             "(predicate, entity) pair. Background mentions are not earlier than gold on average (the independent review "
             "measured mean day 86 against 65); there are simply several of them per pair, and the earliest of three to "
             "nine draws precedes the previous link about half the time for private entities and three quarters of the "
             "time for global ones, so the chain-order test fails. Under hybrid timing a link is dated at its heaviest "
             "witness cluster and a single-witness link becomes one DP state per cluster: tighter for strong links, looser "
             "only for single-witness links. It is free (no calls, no new candidates) and held on the held-out seeds "
             "(+0.04 at 10k with no seed worse, +0.06 at 50k on 3/3) while its cousin modal timing did not (+0.04 on "
             "calibration, −0.01 held-out — rejected). Two things the review established are withdrawn or flagged: the "
             "calibration-seed 'improved decoy resistance' did not replicate — the stale-chain family D5 rises under the "
             "adopted arm (+0.12 at 10k, +0.13 at 50k, seven of eight held-out seeds), because a staleness gate that one "
             "routine positive mention after a retraction defeats was being masked by min timing by accident; and the lag "
             "features the ranker reads are still computed from min timing, which the refit learned around and which is "
             "to be fixed before the next refit. The in-pool magnitude quoted by the panel (20–22 of 25 patterns) comes "
             "from its own replay, whose code is not in the repository.\n")
    t.append("### 3.1 The funnel, old and new (Mycelic hierarchy)\n")
    t.append(f"#### 10,000 ({_funnel_seeds(10_000)})\n")
    t.append(D.funnel_compare(10_000))
    t.append("\n")
    t.append(D.terminal_loss_compare(10_000))
    t.append(f"\n#### 50,000 ({_funnel_seeds(50_000)})\n")
    t.append(D.funnel_compare(50_000))
    t.append("\n")
    t.append(D.terminal_loss_compare(50_000))
    t.append("\n## 4. The change ledger: everything tried, with its cost\n")
    t.append("Every row is a paired run on identical worlds; Δ columns are variant minus base, compute and calls are "
             "ratios. Status ACCEPTED means the change is in the frozen configuration; rejected means it did not "
             "improve found under the real register cap on the held-out seeds (or improved it only at a cost the "
             "brief rules out); diagnostic means the run measures a ceiling and is not a fix.\n")
    t.append(D.ledger_table())
    t.append("\nNot in the table because they were single calibration-seed screens (logs/research_log.md, iteration 21): "
             "merging descent returns per (predicate, entity) before the kernel reads them (compute −54%, found "
             "0.400 → 0.275; per polarity 0.325) and support-1 sketch bits admitted with cross-region corroboration "
             "(found 0.400 → 0.225: 547 weak candidates flood the triage list). Both stay off; the weak-bit mechanism "
             "needs budget-aware admission before it is worth a paired run, and merging needs the ranker refitted on "
             "merged candidates.\n")
    t.append("### 4.1 Compute cost per accepted change (10k, evaluation seeds)\n")
    t.append(D.compute_per_accepted_change())
    # the ranker step as in the table above: quick_v3_H variant against its unbatched v1 base (as
    # benchmarked) and against the batched re-metered v1 base (like-for-like)
    v1u, acc = D._side("v3_H", 0).values(), D._side("v3_H", 1).values()
    v1b = D._side("prev_batched", "prev_batched").values()
    t.append(f"\nThe biggest single gain is the ranker (with the budget it unlocked): "
             f"{_mean(acc, 'found_anywhere_in_register') - _mean(v1u, 'found_anywhere_in_register'):+.2f} found for "
             f"{_pct(_mean(v1b, 'compute_units'), _mean(acc, 'compute_units'))} compute like-for-like "
             f"({_pct(_mean(v1u, 'compute_units'), _mean(acc, 'compute_units'))} as benchmarked). "
             "Hybrid timing is the cheapest: +0.04 for +1%.\n")
    t.append("## 5. Largest remaining loss stage\n")
    t.append(f"In the new funnel the most common terminal loss for the hierarchy is **{l10[0]}** at 10k "
             f"({l10[1]} of {l10[2]} gold patterns) and **{l50[0]}** at 50k ({l50[1]} of {l50[2]}).\n")
    lz = _loss50()
    t.append("At 10k the register is still the binding stage: coverage is near 0.97, so almost every gold pattern is in "
             "the kernel pool, and the loss is ordering among the ~3,000 candidates that pass the gate for 600 slots. "
             "At 50k the register (2,999 slots) is not binding; coverage (~0.72) is, and it is lost mainly at sketch "
             f"visibility ({lz['sk']} of {lz['n']} gold patterns, {lz['sk_rare']} rare; {lz['span']} of the {lz['inv']} "
             f"sketch-invisible patterns fail causal span < 2), then at the question budget ({lz['qb']}) and extraction "
             f"({lz['ex']}); descent reach loses {lz['dr']}. Those are different problems and the queue in section 9 "
             "treats them separately.\n")
    t.append("## 5b. Limitations the independent review established\n")
    t.append("- **Kernel context.** The kernel reads its whole pool in one call: "
             f"{h['new_max_context_tokens_10000'] / 1e6:.2f}M tokens at 10k and {h['new_max_context_tokens_50000'] / 1e6:.2f}M at 50k "
             "in the frozen configuration (v1: "
             f"{h['old_max_context_tokens_10000'] / 1e6:.2f}M and {h['old_max_context_tokens_50000'] / 1e6:.2f}M), against the "
             "modelled frontier tier's 1M-token context, which the simulator enforces only for the flat controls (A2 is "
             "chunked at 1M). No degradation is modelled for the hierarchy's kernel. v1 already exceeded the limit at 50k; "
             "vNext pushes 10k over it. Any deployment claim needs the kernel read chunked at the context limit (queue).\n"
             "- **Compute conventions.** Batched metering changes no decision and no per-record token, but it was applied "
             "to the new arm only; the like-for-like compute increase is the second convention in section 4.1 and the "
             "call reduction is entirely metering.\n"
             "- **Claims at the kernel.** The exposure counters see only the upward pass; descent returns per-user claim "
             "objects to the kernel and the budget change roughly doubles them (about 30k → 53k objects at 10k, 61k → 127k "
             "at 50k). Raw text stays at 0. The counter is to be extended; `J_mycelic_verified`'s raw-record count is "
             "hard-coded to 0 although its verification round reads raw records at the kernel.\n"
             "- **Ranker hygiene.** The register cut happens before the five triage-context features are filled (they are "
             "constant at cut time, so membership is decided by the other 40 features; the ranker was fitted on "
             "post-enrichment dumps); the controls' dumps contain only their post-cut candidates; under hybrid timing the "
             "lag features still use min timing; the J dump duplicates the min-timing hierarchy candidates. None changes "
             "found, all are fixes for the next refit.\n"
             "- **Fairness of link timing.** The flat controls collapse each (predicate, entity) to one object, on which "
             "hybrid timing is a no-op by construction; a fair test gives them per-user objects and charges the context "
             "(queue).\n"
             "- **Panel reuse.** Section 6.\n")
    t.append("## 6. Did the gains survive held-out seeds? (the honest version)\n")
    t.append("The intended protocol was: choose on seeds 500–502, read once on seeds 0–4 (10k) and 0–2 (50k). That is "
             "not what happened, and the first independent refuter (REVIEWER_ATTACK.md) counted it: seeds 0–4 were "
             "read after roughly eleven accept/reject decisions over some forty configurations, and two frozen knobs "
             "were chosen on those seeds rather than on the calibration seeds — the question budget (both sweeps "
             "under a learned ranker ran on 0–4; the only calibration-seed sweep was the v1 one with the hand ranker) "
             "and the rejection of local re-extraction. The ranker fit and the per-architecture adoption are clean "
             "(only calibration-seed rows enter them). Two candidates that won on the calibration seeds lost on 0–4 "
             "and were dropped — modal link timing (+0.04 → −0.01) and decoy-weighted selection (found −0.06, rare "
             "−0.12) — which is the panel doing its job, and also evidence that decisions were being made on it.\n")
    t.append("What this costs: with a paired standard error of about 0.02 found on five seeds, picking the best of "
             "four to eight arms inflates a null by +0.02 to +0.03. The small accepted deltas (budget 0.65 vs 0.50 "
             "+0.03; hybrid timing +0.04) are therefore not established by the development panel alone; the large one "
             "(ranker + budget + batching, +0.21 on every seed, more than ten standard errors) is. The cure is the "
             "confirmation panel in section 1.2: seeds 5–9 at 10k were never read by any experiment, dump or funnel "
             "before the final rerun, and the OLD rows for them exist in the archive, so that comparison is a clean "
             "single read. Where two legitimate arms differed only inside noise on the development panel (the ranker "
             "refitted under hybrid timing vs the previous one), the pre-registered rule — fit the ranker on the "
             "calibration seeds under the pipeline it will run in — decided, not the numbers.\n")
    fp = D.fresh_panel_numbers()
    if fp:
        dev, conf = h["new_found_anywhere_in_register_10000"], h["new_conf_found_anywhere_in_register_10000"]
        where = ("above both" if fp["found"] > max(dev, conf) else "below both" if fp["found"] < min(dev, conf)
                 else "between")
        t.append(f"One clean read of the frozen configuration on a panel nothing else ever touched exists as a by-product "
                 f"of the staleness-gate test: seeds 10–14 at 10k, found {fp['found']:.3f}, rare recall {fp['rare']:.3f}, "
                 f"evidence coverage {fp['cov']:.3f}, decoy acceptance {fp['decoy']:.3f} (D5 {fp['d5']:.3f}), compute "
                 f"{fp['cu']:.2e} (`quick_strong_fresh.jsonl`, arm `hyb`). It has no OLD counterpart, so it is a level, "
                 f"not a delta; it sits {where} the development-panel ({dev:.3f}) and confirmation-panel ({conf:.3f}) "
                 f"levels in section 1.\n")
    t.append("A budget sweep on the calibration seeds under the frozen ranker and timing was run after the refuter's "
             "report. The two sweeps are printed together so the reader can see whether the calibration seeds agree "
             "with the value the development panel chose:\n")
    t.append("**Calibration seeds (quick_qf_cal.jsonl, frozen ranker and hybrid timing)**\n")
    t.append(D.sweep_table("qf_cal", "qf0.65"))
    t.append("\n**Development panel (quick_qf_v3.jsonl, ranker v3, min timing)**\n")
    t.append(D.sweep_table("qf_v3", "qf0.65"))
    t.append("\nReading: on the calibration seeds the budget is flat between 0.50 and 0.80 (the per-seed spread is "
             "larger than any difference between arms), rare recall and coverage rise from 0.50 to 0.65 and 1.00 "
             "loses both found and rare recall. The calibration seeds neither single out 0.65 nor contradict it; the "
             "honest description of the frozen value is \"inside the calibration-seed plateau, chosen on the "
             "development panel\", and the budget's contribution is bounded by the confirmation panel, not by "
             "either sweep.\n")
    t.append("## 7. Ranker evaluation on the final configuration\n")
    t.append(D.ranker_eval_table())
    t.append("\n## 8. Frozen configuration\n")
    t.append(D.frozen_config_table())
    t.append("\n## 9. Ranked experiment queue (by information value, not size)\n")
    t.append(_ranked_queue())
    t.append("\n## 10. Where the reports are\n")
    t.append("- `docs/MYCELIC_ENTERPRISE.md` / `.pdf` — the full benchmark document, regenerated on the new artifacts.\n"
             "- `docs/MYCELIC_LOSS_ACCOUNTING.md` / `.pdf` — the per-pattern loss accounting, regenerated.\n"
             "- `docs/mycelic_vnext/` — this report, `VNEXT_ARCHITECTURE.md`, `EXPERIMENT_MATRIX.md`, "
             "`LOSS_ACCOUNTING.md`, `REVIEWER_ATTACK.md`, the diagrams `fig_topology.svg` and `fig_loop.svg`.\n"
             "- `research/mycelic/artifacts/v1/` — the archived pre-vNext artifacts the OLD columns come from.\n"
             "- `research/mycelic/logs/research_log.md` — the iteration-by-iteration record, including the negative results.\n")
    t.append("\n## What would change my mind\n")
    t.append(CHANGE_MY_MIND)
    return "\n".join(t)


RANKED_QUEUE = """| rank | experiment | what it distinguishes | expected information | cost | status |
|---:|---|---|---|---|---|
| 1 | Register-forecast question budget at 50k (ask in gain order by distinct anchor until the forecast candidate count reaches α × cap) with descent fan-out 1 | whether 50k coverage is limited by the number of anchors asked (breadth) or by reach per anchor (depth) | high: coverage is the binding 50k stage and the two explanations imply opposite spending; but the funnel (50k, seeds {seeds50}) puts most of its loss at sketch visibility ({sk} of {n} patterns), with {qb} at the question budget and {dr} at descent reach | ~10 paired 50k runs | not run |
| 2 | D5 stale-chain mechanism: a staleness gate that compares the retraction against the link's assertion cluster (not just "strong positives only", which was tried) | whether the D5 rise under hybrid timing is the gate or the timing itself | high: D5 rose 0.26 → 0.78 across the accepted changes | cal screen + refit + fresh-panel read | the "strong positives only" variant was screened (+0.058 found on calibration seeds) and REJECTED on the fresh panel (found −0.035 with the frozen ranker, D5 −0.12; refit +0.02 / +0.02): it trades found for D5. The cluster-aware variant is still open |
| 3 | Kernel read chunked at the tier's context limit (as A2 already is), or an explicit degradation model | whether the vNext gain survives a kernel that cannot read a 1.4M/3.2M-token pool in one call | high: precondition of any deployment claim | code + paired runs at both scales | not run |
| 4 | Fresh evaluation panel (seeds 10–14 at 10k, 5–7 at 50k) read once | whether the development-panel deltas hold; this rerun's seeds 5–9 at 10k are the first such read | high for the small deltas | one suite run | seeds 5–9 done in this rerun |
| 5 | Sketch-corroboration feature for the ranker (foreign-site bit for each link's (entity, predicate)) | whether the ranker's remaining 10k ordering loss is missing information or missing capacity | high: offline AUC 0.74–0.77 for the feature; a null result says capacity | dump + refit + 5 paired runs | not run |
| 6 | Per-user object pools for A2/B4/Y with hybrid timing, context charged | whether the timing gain is the topology's or the controls' object builder's | medium: the panel's item was a null by construction | code + paired runs | not run |
| 7 | Lag features from the DP's chosen times; ranker applied once after enrichment; full-list dumps for the controls; then refit | whether the ranker hygiene defects cost anything | medium | refit + 5 paired runs | not run |
| 8 | Pairwise / top-K ranking objective on the same features | same question as 5 from the objective side | medium | refit only | not run |
| 9 | Budget-aware support-1 sketch bits (admit weak bits only into unused question budget) | how many of the rare patterns lost at the sketch (at 50k, seeds {seeds50}: {sk_rare} of the {sk} sketch losses are rare, against {qb_rare} rare at the budget) weak bits recover without flooding the budget | medium: rare recall is 0.31–0.38 (10k and 50k, current rerun) and the weak-bit screen flooded triage | 5 paired runs | screen failed as implemented |
| 10 | Chain-scoped questions with a second untargeted round for thin answers | whether the lean arm's coverage loss can be bought back for less than the 47% compute it saves | medium | 5 paired runs | not run |
| 11 | Descent-evidence merge per polarity with a refitted ranker | compute −54% claim vs the found loss seen on one seed | medium | dump + refit + paired | one-seed screen only |
| 12 | Live model discrimination on kernel-side candidates (frontier vs small model) | whether the simulated kernel tier understates or overstates what a real model does with the same evidence | high for external validity, no effect on the simulator numbers | API budget | live harness exists |
"""


def _ranked_queue() -> str:
    """RANKED_QUEUE with its funnel counts filled in from loss_funnel.jsonl."""
    return RANKED_QUEUE.format_map(_loss50())


CHANGE_MY_MIND = """- If `H_mycelic_prev` inside the new suite does not reproduce the archived v1 rows to the third decimal, the old-vs-new comparison is contaminated by a code change and every Δ in section 1 is suspect.
- If the learned ranker's out-of-sample AUC in section 7 is not above the hand-set score's, the found gain is a gate artefact and should not survive a different register cap.
- If a fresh set of evaluation seeds (5–9) gives hybrid timing a negative paired delta, it joins modal timing in the rejected column.
- If A2 with the ranker beats the hierarchy at 50k at equal compute (it does not today: 10.2e6 vs 3.9e6 units), the compute argument for the hierarchy is gone and only the privacy argument remains.
- If queue item 1 shows 50k coverage is depth-limited, the lean arm's mechanism is the wrong direction and breadth spending should be reverted.
- If the live discrimination harness shows a frontier model extracting a signal from raw notes that no kernel-side feature carries, the ranker's ceiling is a property of the simulator, not of the design.
- If the stale-chain decoy family D5 keeps rising on a fresh panel after the staleness gate is made cluster-aware, hybrid timing is a relaxation of the temporal check and is withdrawn.
- If chunking the kernel read at the tier's 1M-token context removes the vNext gain, the gain was bought with an unmodelled context and the frozen configuration is not deployable as described.
- If counting descent-returned objects in the exposure metric moves the hierarchy's claim exposure to the level of the centralised triage control, the privacy argument for the hierarchy is the raw-text line alone.
"""


def vnext_architecture() -> str:
    t = []
    t.append("# Mycelic vNext — architecture\n")
    t.append("_The design as frozen for the final rerun. Every mechanism named here is implemented in "
             "`research/mycelic/{systems,ops,calibrator,runner}.py` and exercised by the benchmark; nothing "
             "described is a proposal unless marked as such._\n")
    t.append("![topology](fig_topology.svg)\n")
    t.append("## 1. Topology and node responsibilities\n")
    t.append("The org chart is the topology: USER → TEAM → DEPT → SITE → REGION → ENTERPRISE kernel. vNext keeps it, "
             "not for branding but because the loss accounting found no stage where the levels themselves lose gold: "
             "the losses were in the kernel's policies. The level-marginal experiment (E3b) in the main report is the "
             "evidence that a level can be removed only at a cost in routing precision.\n")
    t.append("| node | holds | computes | sends up | answers down |\n|---|---|---|---|---|\n"
             "| USER | its own raw records (never leave) and its local claim index | local extraction to claims; targeted reads from its own index (metered as index lookup + matched records) | claims (knowledge objects) with lineage | the records that match (entity, target predicates); nothing when a strict read matches nothing |\n"
             "| TEAM / DEPT | knowledge objects of its subtree; anchor index (optionally a Bloom filter) | merge, dedupe by signature, adaptive abstraction | merged objects, entity index | routes a question to the children whose index holds the entity |\n"
             "| SITE | the site sketch: per (entity, predicate) a support-thresholded bit (support ≥ 2 witnesses), total mentions, first/last time | sketch construction (no model call) | sketch entries (≤ 4,000 per site) + counts | routes down; is the seed of a descent when the kernel's triage marks it foreign for the entity |\n"
             "| REGION | merged sketch of its sites | bit union with per-site provenance | merged sketch | routing |\n"
             "| KERNEL | the pool of claims it has been sent, the merged enterprise sketch, the question ledger, the candidate list, the register | triage, question policy, synthesis, verification, ranking | — | the questions |\n")
    t.append("## 2. Data structures\n")
    t.append("```\n"
             "SketchEntry(site, entity) = (total_mentions, bitmask over predicates with support>=2, t0, t1)\n"
             "                           # + weak mask (support 1) when sketch_weak_bits is on (OFF in vNext)\n"
             "KO (knowledge object)     = (pred, anchor, polarity, tmin, tmax, sigs: set[witness signature],\n"
             "                             branches: {level: set[node]}, n_raw, importance, q_tag, neg_tmax, pos_tmax)\n"
             "Hypothesis                = (anchor, chain, preds[], links[] (per-link support, regions, lag),\n"
             "                             conf, verified, contra, n_indep, feat: {45 kernel-side features})\n"
             "Question                  = (anchor, target_preds[], expected_gain, cost_est, branch_hint,\n"
             "                             well_targeted, answered, n_new_evidence)\n"
             "Register                  = top-K hypotheses by conf, K = min(6000, max(600, n_entities))\n"
             "Ledgers (batched metering)= route_ledger[node] -> #questions routed this round;\n"
             "                             read_ledger[user] -> [(index_size, matched, anchor)]\n"
             "```\n")
    t.append("## 3. Messages\n")
    t.append("**Upward.** Claims (KOs) carry a predicate, an entity, a polarity, a time interval, the set of witness "
             "signatures and the branch lineage — never the record text. Sketch entries carry counts and bits. The "
             "benchmark's exposure metrics count the upward pass: raw text leaving a node is 0.0 and the fraction of "
             "claims leaving their node on that pass is unchanged by vNext (0.138 at 10k), because no change touched "
             "what moves up. Descent returns are a second channel the counters do not observe: a queried user answers "
             "with per-user claim objects, and the wider budget roughly doubles the objects the kernel holds (about "
             "30k → 53k at 10k, 61k → 127k at 50k). Extending the counter to that channel is queued.\n")
    t.append("**Downward.** A question names an entity and, optionally, target predicates; it is routed by the "
             "kernel's triage (foreign sites for the entity, home site avoided), then by each node's anchor index, "
             "with per-parent quotas (descent fan-out 3; 5/5/5/8 children per level). A strict read (the lean arm) "
             "returns only the target predicates; the default soft read falls back to every record on the entity "
             "when nothing matches.\n")
    t.append("## 4. Routing\n")
    t.append("```\n"
             "descend(anchor, target_preds, budget_nodes, start_nodes, avoid):\n"
             "    frontier = start_nodes or [root]              # triage seeds: the entity's foreign sites\n"
             "    for level in SITE, DEPT, TEAM, USER:\n"
             "        children = [c for n in frontier for c in index_children(n, anchor)] - avoid\n"
             "        rank children by (index hit count, importance prior); keep per-parent quota\n"
             "        route_ledger[n] += 1 for each parent n          # one routing call per node per round\n"
             "        frontier = kept children\n"
             "    for u in frontier at USER:\n"
             "        sel = u.index[anchor]; if target_preds: sel = sel[pred in target_preds] (strict) or fallback\n"
             "        read_ledger[u].append(len(sel)); return claims(sel) with lineage\n"
             "flush_descent_ledger(): meter one call per routed node and one read per queried user\n"
             "```\n")
    t.append("## 5. Question policy\n")
    t.append("```\n"
             "question_round(hyps, pool):\n"
             "    Q = []\n"
             "    for h in hyps by conf desc:                          # 1. hypothesis-driven questions\n"
             "        missing = chain predicates adjacent to h.preds not in h.preds\n"
             "        Q += Question(h.anchor, missing, gain=f(conf, n_indep, contra), well_targeted=True)\n"
             "    for entity in merged sketch:                         # 2. sketch triage (no model call)\n"
             "        foreign = sites holding <= 35% of the entity's mentions\n"
             "        if |foreign| < 2 or |regions(foreign)| < 2: continue\n"
             "        span, chain = longest single-chain run in OR(foreign bits)\n"
             "        if span < 2: continue\n"
             "        gain = (0.6*span + 0.35*|regions| + 0.3*[unseen]) / (1 + 0.25*log1p(total))\n"
             "        Q += Question(entity, target_preds=[] | chain preds (lean), gain, seeds=foreign, avoid=home)\n"
             "    Q.sort(by gain / cost); nq = min(4000, max(220, 0.65 * |Q|))        # frozen budget\n"
             "    for q in Q[:nq]: pool += descend(q.anchor, q.target_preds, fanout=3, q.seeds, q.avoid)\n"
             "    flush_descent_ledger()\n"
             "    hyps = synthesize(pool, link_time='hybrid'); annotate_anchor_context(hyps); apply_ranker(hyps)\n"
             "```\n"
             "The budget is a fraction of the triage queue so it scales with the number of entities the sketch "
             "flags (about 30× more at 50k than at 2k). 0.65 was chosen on the development panel (seeds "
             f"{_quick_seeds('qf_v3')} at 10k), where the paired sweep saturates; on the calibration seeds found is "
             "flat from 0.50 to 0.80.\n")
    t.append("## 6. Candidate generation (kernel)\n")
    t.append("```\n"
             "synthesize(pool):\n"
             "    group claims by (entity after entity-check, predicate)\n"
             "    for entity, for each causal chain with >= 2 of its predicates present:\n"
             "        links = chain-ordered predicates; drop links below min support (dedup check)\n"
             "        # hybrid timing (vNext): cluster each link's claims into <= 3-day windows;\n"
             "        # a link with a cluster of >= 2 independent witnesses gets ONE state at that\n"
             "        # cluster's time; a link whose witnesses disagree gets one state per cluster\n"
             "        states = [(link, t_cluster, w = log1p(support) + 0.35)]\n"
             "        path = heaviest chain-ordered, time-ordered path over states (t_j <= t_i + 3)\n"
             "        if staleness (retracted after asserted): drop\n"
             "        emit Hypothesis(entity, chain, path[:synth_depth], features)\n"
             "    hallucinations at the tier's rate; anchor-level unverified lump when the causal check fails\n"
             "```\n")
    t.append("## 7. Verification and ranking\n")
    t.append("Four checks with tier-dependent pass probabilities (causal, temporal, dedup, entity), the optional "
             "evidence verification round of `J_mycelic_verified` (which writes the attribution feature), then the "
             "ranker: `k = #candidates with hand_conf ≥ 0.5`; candidates are sorted by the learned probability `p`; the "
             "top k get `conf = 0.5 + 0.5p`, the rest `0.5p`; the register keeps the top K by conf. The ranker is a "
             "logistic (l2 and interaction set chosen leave-one-seed-out on the calibration seeds) over 45 features "
             "the kernel already holds; the depth-2 boosted trees lost to it every time and were not adopted.\n")
    t.append("## 8. Privacy boundaries\n")
    t.append("- Raw records: user node only. Re-extraction (rejected) would have re-read them locally; nothing "
             "proposed moves them.\n- Claims: leave the user node as (predicate, entity, polarity, interval, witness "
             "signatures, lineage). This is the benchmark's `claim_exposure_fraction`.\n- Sketch entries: counts and bits; "
             "no entity text beyond the entity id the enterprise already shares.\n- Ranker features: every feature is a "
             "function of the claims and sketch the kernel already holds; the anchor-context features are kernel "
             "aggregates (how many candidates share the entity, its triage gain), not per-user data.\n- No "
             "centralisation is hidden in vNext: the controls that centralise (A2, B4, Y) are run as such and labelled. "
             "What the kernel does hold is per-user claim objects returned by descents, and vNext doubles them; the "
             "exposure counters do not yet count that channel (section 3).\n")
    t.append("## 9. Model allocation, caching, complexity, failure handling\n")
    t.append("- **Allocation**: back-loaded tiers (small models at USER/TEAM extraction, frontier at the kernel) as in v1; "
             "E3 in the main report is the ablation.\n- **Caching / batching**: routing and user reads are metered once "
             "per node per round (the ledgers); the kernel re-reads its pool once per synthesis. Decisions and per-record "
             "tokens are identical to unbatched metering — that is why calls fall "
             f"{_call_drop(10_000):.0f}% at 10k and {_call_drop(50_000):.0f}% at 50k against v1 while compute rises "
             "with the budget.\n"
             "- **Complexity**: triage is O(entities in sketch) integer work; questions O(nq × fan-out × levels) routing "
             "calls; synthesis O(pool + Σ states²) with ≤ ~15 states per (entity, chain); ranking O(candidates × 45). "
             f"The kernel reads its whole pool in one call — {_ctx_range(10_000)} tokens at 10k and "
             f"{_ctx_range(50_000)} at 50k in the frozen configuration, above the modelled tier's 1M context, which "
             "the simulator enforces only for the flat controls; a chunked kernel read is queued before any deployment claim.\n"
             "- **Failure handling**: unavailable branches and malicious nodes are E5 in the main report (unchanged by "
             "vNext); the lean arm's failure mode — the sketch names the wrong chain for ~16% of gold entities and a strict "
             "read then returns nothing — is why it is a separate arm and not the default.\n")
    t.append("![loop](fig_loop.svg)\n")
    t.append("## 10. What is proposed but not implemented\n")
    t.append("Register-forecast budgeting with fan-out 1 at 50k, the sketch-corroboration ranker feature, budget-aware "
             "weak sketch bits, and a pairwise ranking objective — see the ranked queue in `NEXT_RESEARCH_REPORT.md`.\n")
    return "\n".join(t)


HYPOTHESES = [
    # id, area, title, mechanism, expected gain, privacy, compute, failure mode, ablation, status
    ("H01", "ranking/calibration", "Learned ranker over kernel-side features, hand gate count preserved",
     "logistic on 45 kernel-held features decides WHICH candidates fill the hand-gated count", "+0.10–0.20 found at 10k",
     "none (no raw text, kernel aggregates only)", "zero calls; one fit on calibration seeds", "over-fits 3 seeds; promotes decoys",
     "quick_v3_H: ranker off vs on, 5 paired seeds", "ACCEPTED (+0.21 with budget; decoy acc. +0.15)"),
    ("H02", "active question selection", "Question budget as 0.65 of the triage queue", "widen the budget once ordering holds",
     "+0.03 at 10k over 0.50; saturates on development seeds {qf_seeds}, flat 0.50–0.80 on calibration seeds", "none", "+13% compute per +0.15 of budget", "floods register when ranking is weak",
     "quick_qf_v3 sweep 0.50/0.65/0.80/1.00", "ACCEPTED at 0.65; 0.80/1.00 rejected"),
    ("H03", "caching/batching", "Batched metering per routed node and queried user", "ledgers; identical decisions",
     "calls {h03_calls}, compute {h03_cu} at equal budget", "none", "negative", "none observed", "quick_rk2_qf0.65 vs quick_rx_base065",
     "ACCEPTED"),
    ("H04", "candidate generation", "Hybrid link timing in the temporal DP", "date links at heaviest witness cluster; float singletons",
     "+0.04–0.08 found, rare +", "none", "zero", "long-spread real chains could pick a minority time",
     "quick_refit_hyb (5 held-out seeds)", "ACCEPTED (+0.04, CI [0, +0.09])"),
    ("H05", "candidate generation", "Modal link timing", "date links at heaviest cluster only", "+0.025–0.05",
     "none", "zero", "ties pick earliest = old behaviour", "quick_refit_mod", "REJECTED (−0.01 held-out; +0.04 on cal seeds)"),
    ("H06", "ranking/calibration", "Decoy-weighted ranker selection", "up-weight decoy rows, penalise decoys in LOSO objective",
     "decoy acc. −0.05", "none", "zero", "fits 3 seeds' decoys", "quick_seldw_H", "REJECTED (found −0.06, rare −0.12)"),
    ("H07", "evidence reconstruction", "Targeted local re-extraction at the user", "re-extract records naming the anchor not yet claimed",
     "+0.04 at 50k?", "none (stays local)", "+1% compute, 6× wall time", "adds echo evidence", "quick_rx_reextract; quick_v50_v3 rx arm",
     "REJECTED (no gain at 10k; {h07_50k} at 50k, seeds {h07_seeds})"),
    ("H08", "descent/routing", "Chain-scoped triage questions (strict read of flagged chains)", "target_preds = chains with span ≥ 2",
     "compute −47%, found ±0", "none", "negative", "sketch names wrong chain for ~16% of gold entities; coverage −0.17",
     "quick_cal_screen span2; quick_refit_hyb lean arm", "KEPT AS H_mycelic_lean (Pareto arm), not default"),
    ("H09", "richer sketches", "Support-1 sketch bits with cross-region corroboration", "weak site bits admitted only with a strong foreign bit elsewhere",
     "rare recall +", "none", "+11% compute", "floods triage (547 weak candidates on one seed)", "logs iteration 21, one cal seed",
     "REJECTED as implemented (found 0.40 → 0.225)"),
    ("H10", "caching/batching", "Merge descent returns per (predicate, entity[, polarity]) before the kernel reads",
     "one KO per pair", "compute −54%", "none", "negative", "loses per-user time structure the DP needs", "logs iteration 21, one cal seed",
     "REJECTED (found 0.40 → 0.275 / 0.325)"),
    ("H11", "ranking/calibration", "Per-entity diversity in the register", "cap same-anchor candidates", "+0.02", "none", "zero",
     "freed slots go to junk", "offline rescoring (scratch)", "REJECTED (no gain)"),
    ("H12", "ranking/calibration", "Depth-2 gradient-boosted trees instead of logistic", "same features", "AUC +", "none", "zero",
     "3 seeds too few", "select_and_store grid (both selections)", "REJECTED (lost LOSO found both times)"),
    ("H13", "active question selection", "Register-forecast budget by distinct anchor with fan-out 1 (breadth over depth)",
     "ask until forecast candidates reach α × cap", "50k +0.10 at ≤ +20% compute (panel estimate)", "none", "+19% at 50k (est.)",
     "rare patterns in unreached teams", "paired 50k runs", "NOT RUN (queue #1)"),
    ("H14", "ranking/calibration", "Sketch-corroboration feature per link", "foreign-site bit count for (entity, pred)",
     "+0.025 alone (offline)", "none", "zero", "rare patterns never set a bit", "dump + refit + paired", "NOT RUN (queue #5)"),
    ("H15", "ranking/calibration", "Pairwise / top-K ranking objective", "optimise the cap directly", "unknown", "none", "zero",
     "noisier fit", "refit only", "NOT RUN (queue #8)"),
    ("H16", "active question selection", "Second untargeted round for thin strict answers", "re-ask when < 2 on-chain preds returned",
     "lean coverage +", "claims leave node (as today)", "+7%", "re-floods register", "paired", "NOT RUN (queue #10)"),
    ("H17", "temporal/causal reasoning", "Interval order test (tmin_j ≤ tmax_i + slack)", "loosen the DP edge test", "+0.05–0.08 but decoy +0.10–0.15",
     "none", "zero", "D2 scrambled decoys pass", "quick_paired link_time=interval", "NOT RUN (panel predicts decoy leak; low priority)"),
    ("H18", "contradiction handling", "Per-chain enumeration when the causal check fails", "emit one unverified hypothesis per chain",
     "≤ +0.01", "none", "negligible", "more junk", "paired", "NOT RUN (low information)"),
    ("H19", "topology changes", "Remove a hierarchy level", "route from SITE directly to USER", "compute −; precision −",
     "none", "negative", "routing precision", "E3b in the main report", "MEASURED in v1: each level carries routing precision"),
    ("H20", "evidence reconstruction", "Hybrid timing for the flat controls with per-user object pools", "keep per-user objects in A2/B4/Y, charge the context, pass hybrid timing",
     "unknown (on the current one-object-per-pair pools hybrid is a no-op by construction)", "none", "+context", "—", "paired on A2/B4/Y", "NOT RUN (queue #6, rewritten after review)"),
    ("H21", "temporal/causal reasoning", "Cluster-aware staleness gate: only a positive with ≥ 2 independent witnesses counts as the chain's assertion time",
     "ops.synthesize stale_rule='strong'; one routine positive after a retraction no longer revives a stale chain", "D5 down, found ±0", "none", "zero",
     "a genuinely re-asserted chain with single-witness positives is dropped", "quick_stale_cal (cal), quick_strong_fresh (seeds 10-14)",
     "REJECTED: cal seeds +0.058 found / D5 −0.07 did not replicate on the fresh panel (seeds 10–14): frozen ranker found −0.035 (0/3 better) with D5 −0.12; refitted ranker found +0.02 (noise) with D5 +0.02 — it trades found for D5 rather than fixing the mechanism"),
]


def _matrix_numbers() -> dict:
    """The measured cells of HYPOTHESES, from the same paired files the ledger reads."""
    def minus(s):
        return s.replace("-", "−")
    # H03: batched metering alone, identical decisions (ledger row "batched descent metering alone")
    a, b = D._arms("rk2_qf0.65|rx_base065", 1, 1)
    common = sorted(set(a) & set(b))
    ra, rb = [a[s] for s in common], [b[s] for s in common]
    # H07: local re-extraction at 50k, per-seed Δ found against the qf 0.65 arm
    x, y = D._arms("v50_v3", "H_v3_qf0.65", "H_v3_qf0.65_rx")
    s50 = sorted(set(x) & set(y))
    return {"qf_seeds": _quick_seeds("qf_v3"),
            "h03_calls": minus(_pct(_mean(ra, "inference_calls"), _mean(rb, "inference_calls"))),
            "h03_cu": minus(_pct(_mean(ra, "compute_units"), _mean(rb, "compute_units"))),
            "h07_50k": minus("/".join(f"{y[s]['found_anywhere_in_register'] - x[s]['found_anywhere_in_register']:+.2f}"
                                      for s in s50)),
            "h07_seeds": _span(s50)}


def experiment_matrix() -> str:
    t = ["# Mycelic vNext — experiment matrix\n",
         "_Twenty hypotheses across the ten areas the pack asked for, each with mechanism, expected gain, privacy "
         "risk, compute cost, failure mode and minimal ablation; the status column is the measured result where one "
         "exists (paired on identical worlds, held-out seeds unless marked cal). The five highest-information "
         "experiments and the ranked queue follow._\n",
         "## 1. Hypotheses\n",
         "| id | area | hypothesis | mechanism | expected gain | privacy risk | compute | failure mode | minimal ablation | status |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    vals = _matrix_numbers()
    for h in HYPOTHESES:
        t.append("| " + " | ".join(c.format_map(vals) for c in h) + " |")
    t.append("\n## 2. The five highest-information experiments (chosen, and what they said)\n")
    t.append("1. **Ranker on vs off, hand gate count preserved, four architectures** — distinguishes \"the register cut "
             "is ordering\" from \"the register cut is the gate\". Result: ordering; every architecture gained.\n"
             "2. **Question budget sweep with the fixed ranker** — distinguishes \"the budget was starved\" from \"the "
             "budget was right and widening only floods\". Result: starved up to 0.65, flooding beyond.\n"
             "3. **Unbounded register + full budget (diagnostic only)** — the ceiling the pool allows (0.785 at 10k with "
             "budget 0.65); says how much of the gap is ordering versus evidence. Result: at 10k almost all ordering.\n"
             "4. **Link timing: modal vs hybrid, calibration then held-out** — distinguishes \"gold is lost to timing "
             "contamination\" from \"gold is lost to the order test being too tight\". Result: contamination (hybrid "
             "helps; modal, which also fixes contamination but keeps one time per link, does not replicate).\n"
             "5. **Old vs new at 50k with re-extraction and budget arms** — distinguishes \"50k is a register problem\" "
             "from \"50k is a coverage problem\". Result: coverage.\n")
    t.append("## 3. Ranked queue\n")
    t.append(_ranked_queue())
    t.append("\n## 4. Benchmark plan (as executed)\n")
    t.append("- Worlds: the benchmark's evaluation seeds 0–9 at 2k/10k (0–4 development, 5–9 confirmation) and 0–4 "
             "at 50k for the headline suite; 0–2 for the 50k sweeps; calibration seeds 500–502 for the ranker, its "
             "per-architecture adoption and the calibration screens (link timing was screened there first); the question "
             "budget and the rejection of local re-extraction were chosen on the development panel (seeds 0–4 at 10k, "
             "0–2 at 50k), decoy-weighted ranker selection and modal link timing were rejected on seeds 0–4 at 10k, and "
             "the frozen-configuration evidence for question_frac, batched_descent, link_time and local_reextract was "
             "read on those seeds (report sections 4, 6 and 8).\n- Controls on identical worlds: A_flat_rag, "
             "A2_chunked_ctx, B_long_context, B2_map_reduce, B4_central_triage, C_recursive_sum, the hierarchy family "
             "D–J, H_mycelic_prev (v1), H_mycelic_lean, the oracles Y/Z/Z2.\n- Every change is a paired run first "
             "(`quick_paired` / `arm_paired`), then the suite.\n- Ranker adoption per architecture on the calibration "
             "seeds; the hand-set score is a ranker too.\n- Register cap, metrics, gold and worlds untouched; the "
             "unbounded register is used only as a diagnostic.\n")
    t.append(D.ledger_table())
    return "\n".join(t)


def loss_accounting_doc() -> str:
    t = ["# Mycelic vNext — loss accounting (old vs new)\n",
         "_The full per-pattern accounting, with the stage definitions, the sketch-failure taxonomy, the witness curve "
         "and the rank tables, is `docs/MYCELIC_LOSS_ACCOUNTING.md` (regenerated on the new artifacts). This file "
         "puts the old and new funnels side by side for the hierarchy._\n",
         "Stages (each row is a gold pattern; a stage is passed only if every earlier stage was): extracted → "
         "sketch_visible → in_triage → questioned → descent_reached → in_pool → candidate → matched_any → "
         "matched_tau → in_register. `loss_stage` is the terminal loss (first failing stage after the last passing one).\n",
         f"## 10,000 ({_funnel_seeds(10_000)})\n", D.funnel_compare(10_000), "\n### Where they died\n",
         D.terminal_loss_compare(10_000),
         f"\n## 50,000 ({_funnel_seeds(50_000)})\n", D.funnel_compare(50_000), "\n### Where they died\n", D.terminal_loss_compare(50_000),
         "\n## Reading\n",
         "The pack's eight loss categories map onto the columns as: never represented in sketches = ¬sketch_visible; "
         "filtered from knowledge objects = extracted but ¬in_pool after reach; not selected for questioning = "
         "¬questioned; descent routing miss = ¬descent_reached; local extraction miss = ¬extracted; evidence at "
         "kernel but no candidate = in_pool ∧ ¬candidate; candidate rejected by verification = candidate ∧ "
         "¬matched_tau; ranking buried it = matched_tau ∧ ¬in_register.\n"]
    return "\n".join(t)


def _flat_rag_adoption() -> str:
    """Attack 3's A_flat_rag sentence, read from calibration.json's ranker_arch_table and the headline rows."""
    p = os.path.join(ART, "calibration.json")
    tab = None
    if os.path.exists(p):
        with open(p) as fh:
            tab = json.load(fh).get("ranker_arch_table", {}).get("A_flat_rag")
    if not tab or not tab.get("adopt"):
        return "A_flat_rag kept the hand score by the same rule."
    old = [r for s, r in D.by_seed(D.e1_rows(D.V1), "A_flat_rag", 10_000).items() if s < 5]
    new = [r for s, r in D.by_seed(D.e1_rows(), "A_flat_rag", 10_000).items() if s < 5]
    return (f"A_flat_rag adopted it by the same rule (calibration seeds: found {tab['found_ranker']:.3f} with the "
            f"ranker vs {tab['found_hand']:.3f} with the hand score), and its AP at 10k (seeds 0–4) went from "
            f"{_n(_mean(old, 'average_precision'))} to {_n(_mean(new, 'average_precision'))}.")


def reviewer_attack() -> str:
    h = _hn()
    lz = _loss50()
    t = ["# Mycelic vNext — adversarial review\n",
         "_Written to falsify the claims in `NEXT_RESEARCH_REPORT.md`. Each attack is followed by what the artifacts "
         "say and what was done about it. Where an attack lands, it says so._\n",
         "## Attack 1 — the gains are tuned on the evaluation seeds\n",
         "The calibration seeds are 500–502; the evaluation seeds are 0–9 at 10k (development 0–4, confirmation 5–9) and "
         "0–4 at 50k (0–2 for the sweeps). Every knob in the frozen "
         "configuration carries the tag of the paired file that confirmed it (`frozen_config_table` in the report). Two "
         "candidates that won on the calibration seeds lost on the evaluation seeds and were dropped, which is the "
         "protocol working, not a coincidence. **Where it lands**: the evaluation seeds were read more than once "
         "during this work (each accepted change was read on them). That is sequential testing on a fixed panel; "
         "the report's numbers are therefore optimistic by an amount five paired seeds cannot bound. Mitigation "
         "offered: the third item under *What would change my mind* is a fresh seed panel.\n",
         "## Attack 2 — the ranker is a simulator artefact\n",
         "Its features are exact quantities the simulator hands the kernel (witness signatures, lag, lineage). A real "
         "kernel would estimate them from claims with noise. The live-model harness in the main report measures the "
         "discrimination a frontier model achieves from raw notes; it does not yet measure discrimination from kernel-side "
         "claims. **Lands partly**: the *size* of the ranker gain is a simulator number; the *direction* (ordering was "
         "the binding stage) rests on the funnel, which counts gold patterns, not on the ranker.\n",
         "## Attack 3 — the baselines were handicapped\n",
         f"The centralised controls adopt the ranker only where their own calibration says it is not worse; A2 adopted "
         f"it and went from {_n(h['a2_old_found_anywhere_in_register_10000'])} to "
         f"{_n(h['a2_found_anywhere_in_register_10000'])} at 10k. {_flat_rag_adoption()} "
         "The old A2 rows are in the OLD column of the same tables. **Does not land** on the comparison; it does mean "
         "the \"gap to A2\" moved less than the hierarchy's own gain.\n",
         "## Attack 4 — metric gaming\n",
         "Register cap: unchanged (`min(6000, max(600, n_entities))`); the unbounded register appears only in rows "
         "labelled diagnostic. Gold, worlds, seeds, metrics: unchanged (the funnel code was verified by three "
         "independent refuters before the work started, and the corrections it needed are in the research log). "
         "Question budget: a fraction of the triage queue, not of the number of gold patterns. **Does not land.**\n",
         "## Attack 5 — hidden centralisation\n",
         "Nothing new moves up and raw text leaving a node is 0.0. But the claim-exposure fraction that reads *same* "
         "in the paired tables counts only the upward pass: descent returns per-user claim objects to the kernel and "
         "the wider budget roughly doubles them (about 30k → 53k objects at 10k, 61k → 127k at 50k), which is also "
         "where most of the extra compute goes. The ranker's anchor-context features are counts over candidates the "
         "kernel already holds; re-extraction, the one change that touched raw records, stayed local and was rejected. "
         "**Lands partly**: the counter is blind to the channel the accepted change widened, and the privacy sections "
         "now say so.\n",
         "## Attack 6 — the decoy regression is being minimised\n",
         f"It is not: decoy acceptance at 10k went from {_n(h['old_decoy_acceptance_all_10000'])} to "
         f"{_n(h['new_decoy_acceptance_all_10000'])} and the one fix tried (decoy-weighted selection) was rejected "
         "because it cost found and rare recall. FDR fell slightly. **Lands**: a register with more true patterns "
         "and more decoys is a better register only if the reader's cost of a decoy is below the value of a pattern; "
         "the report does not claim otherwise.\n",
         "## Attack 7 — the hierarchy still loses to centralised discovery\n",
         f"Yes: {_n(h['new_found_anywhere_in_register_50000'])} vs {_n(h['a2_found_anywhere_in_register_50000'])} at "
         f"50k, at {h['new_compute_units_50000'] / max(1e-9, h['a2_compute_units_50000']):.2f}× of A2's compute. The "
         "brief's targets (0.70 / 0.60) were not met. The report says which stage holds the rest (ordering at 10k; "
         f"coverage at 50k, lost mainly at sketch visibility: {lz['sk']} of {lz['n']} gold patterns) and what would "
         "test it. **Lands.**\n",
         "## Attack 8 — single-seed screens are being cited\n",
         "Two negative results (weak sketch bits, descent-evidence merge) rest on one calibration seed. They are cited "
         "as screens that stopped further spending, not as findings, and they are not in the ledger table. "
         "**Lands on wording, addressed.**\n",
         "## Attack 9 — the hybrid DP leaks decoys\n",
         "Letting a link float across its clusters relaxes the order test for single-witness links. The scrambled-time "
         "family D2 is flat (+0.02 at 10k, +0.01 at 50k), but the stale-chain family D5 rises under the adopted arm on "
         "seven of eight held-out seeds (+0.12 at 10k, +0.13 at 50k; 0.26 → 0.78 across all the accepted changes at 10k). "
         "The independent review traced it to the staleness gate, which one routine positive mention after a retraction "
         "defeats and which min timing was masking by accident. **Lands**: the 'improved decoy resistance' claim is "
         "withdrawn, all four families are in every table, and a cluster-aware gate is queue item 2.\n",
         "## Attack 10 — the compute comparison mixes metering conventions\n",
         "It did: the v1 base was metered unbatched and the new arm batched; batching alone is −17% compute and −84% "
         "calls at identical decisions. **Lands**: the v1 base was re-metered batched on the same worlds and the report "
         "gives both conventions; the call reduction is labelled as metering.\n",
         "## Attack 11 — the kernel prompt exceeds the modelled context\n",
         f"{_ctx_range(10_000)} tokens at 10k and {_ctx_range(50_000)} at 50k in one call against a 1M context that "
         "the simulator enforces "
         "only for the flat controls; the report's kernel-context metric showed the first stage only. **Lands**: stated "
         "in section 5b of the report, printed in the tables, chunking queued as a precondition of any deployment claim.\n",
         "## Revision after the review\n",
         "- Kept: the three accepted changes and the lean arm as a labelled Pareto point.\n"
         "- Reworded: the single-seed screens; the decoy regression is stated in the first paragraph of the report.\n"
         "- Added to the queue, and read once in the final rerun (report section 1.2): a fresh evaluation panel "
         "(seeds 5–9 at 10k) before any of the reported deltas is quoted outside this repository.\n"]
    ind = os.path.join(ROOT, "research", "mycelic", "logs", "independent_review.md")
    if os.path.exists(ind):
        t.append("## Independent review\n")
        t.append("_Three refuters were run as separate agents with read-only access to the repository, each "
                 "told to falsify one group of claims (protocol and leakage; the hybrid DP and decoys; metrics, "
                 "cap and funnel integrity). Their reports follow verbatim, each with the author's response._\n")
        with open(ind) as fh:
            t.append(fh.read())
    t += ["## What would change my mind\n", CHANGE_MY_MIND]
    return "\n".join(t)


def build() -> None:
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "fig_topology.svg"), "w") as fh:
        fh.write(TOPOLOGY_SVG)
    with open(os.path.join(DOCS, "fig_loop.svg"), "w") as fh:
        fh.write(LOOP_SVG)
    parts = []
    for name, fn in (("NEXT_RESEARCH_REPORT.md", next_research_report),
                     ("VNEXT_ARCHITECTURE.md", vnext_architecture),
                     ("EXPERIMENT_MATRIX.md", experiment_matrix),
                     ("LOSS_ACCOUNTING.md", loss_accounting_doc),
                     ("REVIEWER_ATTACK.md", reviewer_attack)):
        txt = fn()
        with open(os.path.join(DOCS, name), "w") as fh:
            fh.write(txt)
        print("wrote", os.path.join(DOCS, name), len(txt), "chars")
        parts.append(txt)
    # one file for the PDF: the five documents in order, page-broken
    allp = os.path.join(DOCS, "ALL.md")
    with open(allp, "w") as fh:
        fh.write('\n\n<div style="page-break-after: always"></div>\n\n'.join(parts))
    print("wrote", allp)


if __name__ == "__main__":
    build()

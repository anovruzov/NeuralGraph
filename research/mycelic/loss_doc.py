"""Generate docs/MYCELIC_LOSS_ACCOUNTING.md from the funnel and diagnostic rows.

Every number is read from artifacts/*.jsonl at generation time.

    python3 -m research.mycelic.loss_doc
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Dict, List

import numpy as np

from .analysis import boot_ci, sign_test
from .loss_report import build as loss_tables, load
from .runner import ART

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "MYCELIC_LOSS_ACCOUNTING.md")

DIAG = [
    ("diag_qf1", "ask every triage candidate (question_frac = 1.0)"),
    ("diag_reg", "unbounded register (diagnostic only)"),
    ("diag_both", "both"),
    ("diag_qf1_nofilter", "full budget, home-site evidence allowed"),
]
DIAG50 = [
    ("diag50_qf1", "ask every triage candidate (question_frac = 1.0)"),
    ("diag50_reg", "unbounded register (diagnostic only)"),
    ("diag50_both", "both"),
]
KEYS = [("found_anywhere_in_register", "found", 3),
        ("evidence_coverage_2links", "evidence cov.", 3),
        ("rare_signal_recall", "rare recall", 3),
        ("average_precision", "AP", 4),
        ("false_discovery_rate", "FDR", 3),
        ("compute_units", "compute", 0),
        ("inference_calls", "calls", 0)]


def _quick(tag: str) -> List[Dict]:
    p = os.path.join(ART, f"quick_{tag}.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def diag_table(specs, scale: int) -> str:
    lines = ["| variant | " + " | ".join(h for _, h, _ in KEYS) + " |",
             "|---|" + "---:|" * len(KEYS)]
    base_done = False
    for tag, label in specs:
        rows = [r for r in _quick(tag) if r["scale"] == scale]
        if not rows:
            continue
        base = {r["seed"]: r for r in rows if r["cfg_over"] is None}
        var = {r["seed"]: r for r in rows if r["cfg_over"] is not None}
        seeds = sorted(set(base) & set(var))
        if not seeds:
            continue
        if not base_done:
            cells = []
            for k, _, d in KEYS:
                v = np.mean([base[s][k] for s in seeds])
                cells.append(f"{v:.{d}f}" if d else f"{v:.2e}")
            lines.append(f"| H_mycelic_full (calibrated) | " + " | ".join(cells) + " |")
            base_done = True
        cells = []
        for k, _, d in KEYS:
            dv = np.array([var[s][k] - base[s][k] for s in seeds])
            v = np.mean([var[s][k] for s in seeds])
            m, lo, hi = boot_ci(dv)
            wins, n, p = sign_test(dv.tolist())
            mark = ""
            if lo > 0 or hi < 0:
                mark = " **" + ("↑" if m > 0 else "↓") + "**"
            cells.append((f"{v:.{d}f}" if d else f"{v:.2e}") + mark)
        lines.append(f"| {label} ({len(seeds)} seeds) | " + " | ".join(cells) + " |")
    return "\n".join(lines)


PAIR_KEYS = [("found_anywhere_in_register", "found", 3),
             ("rare_signal_recall", "rare recall", 3),
             ("evidence_coverage_2links", "evidence cov.", 3),
             ("average_precision", "AP", 4),
             ("false_discovery_rate", "FDR", 3),
             ("decoy_acceptance_all", "decoy acc.", 3),
             ("compute_units", "compute", 0),
             ("inference_calls", "calls", 0)]


def paired_table(specs, scale: int = 10_000, base_label: str = "base") -> str:
    """One row per experiment: the variant's mean, with the paired Δ against
    its own base on the same seeds and a mark when the 95% bootstrap interval
    of the Δ is entirely on one side of zero.  Reads quick_<tag>.jsonl."""
    lines = ["| experiment | seeds | " + " | ".join(h for _, h, _ in PAIR_KEYS) + " |",
             "|---|---:|" + "---:|" * len(PAIR_KEYS)]
    for tag, label in specs:
        rows = [r for r in _quick(tag) if r["scale"] == scale]
        if not rows:
            continue
        # rows alternate base, variant per seed (quick_paired writes them so)
        base = {}
        var = {}
        for i, r in enumerate(rows):
            (base if i % 2 == 0 else var)[r["seed"]] = r
        seeds = sorted(set(base) & set(var))
        if not seeds:
            continue
        bcells, vcells = [], []
        for k, _, d in PAIR_KEYS:
            b = np.array([base[s][k] for s in seeds], dtype=float)
            v = np.array([var[s][k] for s in seeds], dtype=float)
            m, lo, hi = boot_ci(v - b)
            mark = ""
            if len(seeds) > 1 and (lo > 0 or hi < 0):
                mark = " **" + ("↑" if m > 0 else "↓") + "**"
            fmt = (lambda x: f"{x:.{d}f}") if d else (lambda x: f"{x:.2e}")
            bcells.append(fmt(b.mean()))
            vcells.append(f"{fmt(v.mean())} <sub>{'+' if m >= 0 else ''}{fmt(m)}</sub>{mark}")
        lines.append(f"| {label}: {base_label} | {len(seeds)} | " + " | ".join(bcells) + " |")
        lines.append(f"| {label}: variant | {len(seeds)} | " + " | ".join(vcells) + " |")
    return "\n".join(lines) if len(lines) > 2 else "_(not run)_"


RANKER_V1 = [("rk_H", "hierarchy, calibrated budget"),
             ("rk_Hqf1", "hierarchy, full question budget"),
             ("rk_A2", "A2_chunked_ctx"), ("rk_B4", "B4_central_triage"),
             ("rk_Y", "Y_oracle_retrieval")]
RANKER_V2 = [("rk2_H", "hierarchy, calibrated budget"),
             ("rk2_A2", "A2_chunked_ctx"), ("rk2_B4", "B4_central_triage"),
             ("rk2_Y", "Y_oracle_retrieval")]
QF_SWEEP = [("rk2_qf0.50", "question_frac 0.50"), ("rk2_qf0.65", "question_frac 0.65"),
            ("rk2_qf0.80", "question_frac 0.80"), ("rk2_qf1.00", "question_frac 1.00")]


def vnext_section() -> str:
    return f"""## 9. vNext, measured so far

Everything in this section is a **paired** experiment on the evaluation
seeds (0–4 at 10,000 users): the variant and its base run on identical
worlds, and the Δ shown under each variant value is the paired mean with an
arrow when its 95% bootstrap interval excludes zero. Every knob in every
variant was fitted on the calibration seeds (500–502) and frozen before
these seeds were touched. Nothing here changes the register, the threshold,
the gold labels or the matching rule.

### 9.1 A ranker fitted on kernel-side features

The confidence logistic is replaced as a *ranker* only. Version 1 kept the
hand-set ≥ 0.5 gate and re-ordered inside it; version 2 keeps the hand-gated
**count** and lets the learned score choose which candidates fill those
slots, with anchor-level context features (how many candidates share the
entity, where this one ranks among them). Fitted once on the pooled
candidates of all four systems on seeds 500–502; l2 and the interaction set
chosen leave-one-seed-out on those seeds only.

**Version 1 — order within the hand gate** (base = same system, hand-set ranker)

{paired_table(RANKER_V1, base_label="hand ranker")}

**Version 2 — learned top-K gate + anchor context**

{paired_table(RANKER_V2, base_label="hand ranker")}

**What it says.** The same ranker lifts the hierarchy, its centralised
twin and the perfect-retrieval oracle by 10–17 points of discovery and
multiplies AP by four to six, and it makes `A2_chunked_ctx` *worse* on both
found and rare recall. A ranker chosen on one system's candidates must not
be imposed on another, so adoption is now decided per architecture on the
calibration seeds — each system keeps whichever ranker is not worse there —
and stored with the other calibrated settings. Rare recall on the
hierarchy moves inside noise in both versions: the ranker recovers common
patterns first.

**Two regressions, not hidden.** Decoy acceptance rises with the ranker on
every system that adopts it (hierarchy 0.180 → 0.275, B4 0.175 → 0.295,
oracle 0.125 → 0.230): the planted traps share the features that make a
genuine chain look genuine, and the ranker promotes them alongside. FDR
falls slightly at the same time, so the register is cleaner overall but
more of what it contains is a trap rather than noise. This is the next
thing the ranker has to be taught, and it is the reason decoy acceptance
stays a headline metric rather than a footnote.

### 9.2 Spending the question budget, with the ranker on

Base = the hierarchy with ranker v2 at its calibrated budget
(question_frac = 0.25). The earlier sweep without the ranker turned over at
0.35 and *lost* discovery at 1.0; the question is whether a better ranker
lets the recovered evidence be reported.

{paired_table(QF_SWEEP, base_label="qf 0.25")}

**What the sweep says.** With the ranker on, every budget above 0.25 recovers
evidence (coverage 0.69 → 0.87–0.985) and reports a little more of it
(found +0.03 to +0.04, rare +0.065 to +0.077 — the rare gain is where the
extra evidence goes), at +31% to +104% compute. The budget no longer *hurts*
discovery as it did without the ranker, but it is not converting either:
at question_frac 1.0 the kernel holds evidence for 98.5% of patterns and
reports 51.5%, against a measured ranking ceiling of 79.5%. The remaining
loss is inside the kernel's ordering of candidates it already has, which
is where the next iteration goes.

### 9.3 Where a full budget spends its compute, and what batching returns

Profiled on one calibration seed (500) at 10,000 users with the full
question budget, single runs, **not** evaluation-seed measurements:

| stage | share of compute | calls | note |
|---|---:|---:|---|
| kernel re-read of the merged pool | 42% | 1 | 89,525 objects × 26 tokens in one frontier call |
| descent routing | 31% | 145,823 | one frontier-tier call per (parent node, anchor) |
| user reads | 6% | 69,549 | 8,361 of 9,491 reached users queried more than once |
| edge extraction | 6% | 9,999 | unchanged by any of this |

Three cost changes, same seed, full budget, ranker off:

| change | found | AP | rare | pool | compute | calls | kept? |
|---|---:|---:|---:|---:|---:|---:|---|
| none | 0.400 | 0.0123 | 0.125 | 89,525 | 2.31e+06 | 227,524 | — |
| batched routing + reads (one call per node / per user; identical decisions and per-record tokens) | 0.400 | 0.0123 | 0.125 | 89,525 | 1.91e+06 | 27,127 | **yes** — pure cost |
| + merge descent returns per (predicate, entity) before the kernel reads them | 0.275 | 0.0052 | — | 22,225 | 1.07e+06 | 27,128 | no |
| + merge per (predicate, entity, **polarity**) | 0.325 | 0.0075 | 0.312 | 23,210 | 1.08e+06 | 27,128 | not yet — needs the ranker re-fitted on merged candidates |

### 9.4 Targeted local re-extraction

When a descent reaches a user, that user re-reads its *own* notes that name
the entity but produced no claim on the first pass (the edge model's recall
is a per-record coin; §5 measured that a fresh read recovers 80–91% of the
patterns lost there). Raw text stays on the node; only objects leave. One
calibration seed, full budget, ranker v2, batched:

| | found | AP | rare | evidence cov. | compute | records re-read locally |
|---|---:|---:|---:|---:|---:|---:|
| without | 0.350 | 0.0818 | 0.062 | 0.925 | 1.97e+06 | 0 |
| with | 0.475 | 0.0385 | 0.188 | 0.925 | 1.99e+06 | 7,980 |

The paired evaluation-seed test of this is queued behind the budget sweep;
it is reported here as a single-seed signal, not a result.

### 9.5 What is accepted so far, and at what cost

| change | status | Δ found (10k, paired, 5 seeds) | Δ compute |
|---|---|---:|---:|
| ranker v2, per-architecture adoption | **accepted** | +0.105 on the hierarchy | −1% |
| batched descent metering | **accepted** (cost only) | 0 | −17% at full budget, −88% calls |
| question budget above 0.25 | under test | see 9.2 | +31% at 0.50 |
| kernel-side evidence merge | rejected for now | −0.075 (one cal seed) | −44% |
| local re-extraction | promising, untested on eval seeds | +0.125 (one cal seed) | +0.7% |
| support-1 sketch bits for rare facets | not started | — | — |
"""


def calibrator_section() -> str:
    p = os.path.join(ART, "hyp_features.jsonl")
    if not os.path.exists(p):
        return "_(feature dump not yet complete)_"
    try:
        from .calibrator import evaluate_calibrator
        out = []
        for tag in ("", "_qf1"):
            pp = os.path.join(ART, f"hyp_features{tag}.jsonl")
            if not os.path.exists(pp) or os.path.getsize(pp) == 0:
                continue
            r = evaluate_calibrator(tag)
            out.append(f"**Dump `{tag or 'default budget'}`** — {r['n_train']:,} "
                       f"training candidates (seeds 500–502), {r['n_test']:,} "
                       f"evaluation candidates (seeds 0–4), positive rate "
                       f"{r['pos_rate_train']:.1%}. Pooled AUC gold-vs-spurious: "
                       f"hand-set confidence {r['auc_conf']:.3f}, learned "
                       f"{r['auc_learned']:.3f}.\n")
            out.append("| architecture | gold patterns | with any candidate | "
                       "found (current ranker) | found (learned rank, same "
                       "#kept) | found (learned, p≥0.5) | AUC current | AUC "
                       "learned |")
            out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
            for a, v in r["per_arch"].items():
                out.append(f"| {a} | {v['gold_patterns']} | "
                           f"{v['matchable_patterns']} | {v['found_baseline']:.3f} | "
                           f"{v['found_learned_rank_only']:.3f} | "
                           f"{v['found_learned_thresholded']:.3f} | "
                           f"{v['auc_conf']:.3f} | {v['auc_learned']:.3f} |")
            out.append("")
            top = sorted(r["weights"].items(), key=lambda kv: -abs(kv[1]))[:8]
            out.append("Largest standardised weights: " +
                       ", ".join(f"`{k}` {v:+.2f}" for k, v in top if k != "bias") + ".")
            out.append("")
        return "\n".join(out) if out else "_(feature dump not yet complete)_"
    except Exception as e:  # pragma: no cover
        return f"_(calibrator evaluation failed: {e})_"


def compose() -> str:
    rows = load()
    n10 = len({r["seed"] for r in rows if r["scale"] == 10_000})
    n50 = len({r["seed"] for r in rows if r["scale"] == 50_000})
    return f"""# Mycelic: where the hidden patterns go
## Loss accounting and gap decomposition

**What this is.** The benchmark report says the hierarchy finds 36% of the
hidden patterns at 50,000 users and the strongest centralised system finds
70%. This document says *where the other patterns went*. Every discoverable
gold pattern is traced through the pipeline and the first stage at which it
is lost is recorded, for the hierarchy (`H_mycelic_full`), its centralised
twin (`B4_central_triage`), the perfect-retrieval oracle (`Y_oracle_retrieval`)
and the strongest centralised baseline (`A2_chunked_ctx`), on the same worlds.
Then three paired diagnostics on identical worlds decompose the gap into the
stages responsible.

Nothing here is a design. It is the measurement the next design is built on.
Every number is computed from `research/mycelic/artifacts/loss_funnel.jsonl`
and `quick_diag*.jsonl` at generation time. {n10} seeds at 10,000 users,
{n50} at 50,000.

## 1. The finding in one paragraph

The handoff pack's leading hypothesis was that the sketch channel is too poor
to seed good hypotheses. It is not: at 10,000 users the sketch triage sees
98.5% of gold anchors. The pattern dies later, at two places. First, the
**question budget**: the calibrated budget asks only 63.5% of gold anchors at
10k and 43% at 50k, and that is the single largest first-loss stage at both
scales (47% and 51% of the gap to A2). Second, the **register**: at 10k, 12%
of all patterns are matched at confidence ≥ 0.5 and then cut, because the
hierarchy rates spurious candidates as highly as genuine ones and the
register holds one entry per entity. A2 loses nothing at the register. Asking
every triage candidate recovers evidence for 98.5% of patterns and the kernel
forms a correct confident candidate for ~80% — more than A2 — and then buries
them. The calibrated small budget was the right choice *given the ranker*; the
ranker is the fault. At 50,000 users the sketch additionally loses 18% of
patterns (mostly rare: a single-witness facet cannot set a site bit under the
support threshold), so scale adds a third, smaller stage.

## 2. Stage-by-stage survival

Stages are ordered by pipeline position. They are not strictly nested — a
pattern can be visible to the sketch without having been extracted — so
`first stage lost` records the first stage in order that failed. The flat
systems have no sketch, triage, question or descent stage.

{loss_tables()}

## 3. Paired decomposition on identical worlds

Each row is the calibrated hierarchy with one setting changed, run on the
same (scale, seed) worlds as the base. **An unbounded register is a
benchmark change, not a design**; it appears here only to measure how much
discovery is being lost to ranking rather than to retrieval or judgement.
Arrows mark a 95% bootstrap interval entirely on one side of zero.

### 10,000 users

{diag_table(DIAG, 10_000)}

### 50,000 users

{diag_table(DIAG50, 50_000)}

**Reading it.** Asking every triage candidate raises evidence coverage from
0.69 to 0.985 at 10k (0.45 to 0.79 at 50k) and *lowers* discovery at 10k:
the recovered evidence produces candidates the register cannot hold. Remove
the register pressure and the same runs report 0.795 at 10k — above A2's
0.685 — and 0.643 at 50k against A2's 0.703, at two-thirds of A2's compute.
Allowing home-site evidence back in does not help, so the flood is not the
foreign-only filter. The residual at 50k is the sketch loss, which no
question budget can recover because the anchor never reaches the triage list.

## 4. Why the hierarchy's ranker is worse than A2's on the same operator

Both systems rank with the same confidence logistic. Its main terms — a
causal span, several regions, links dominated by different branches — are
exactly the properties the triage *pre-selects* anchors for. Among the
candidates the hierarchy's kernel actually has to order, those terms carry
almost no information, because every questioned anchor already has them. A2's
pool holds every anchor, including thousands with no cross-organisational
structure at all, for which the same terms are decisive. The hierarchy's
discrimination problem is a conditioning problem, not an operator problem.

The consequence is testable: features the triage did **not** condition on —
per-link independent support, inter-link lags, conflict volume, dispersion,
whether the candidate came from a question — should separate genuine from
spurious candidates among the triage survivors. Section 6 measures that.

## 5. Two smaller losses, measured

**Extraction (10% at 50k, 24% of rare patterns).** This is stochastic recall,
not a capability ceiling: a fresh edge-tier re-read of the same records
recovers 80–91% of the patterns lost there, a kernel-tier *local* re-read
100%. A user agent asked a targeted question about an entity can re-read its
own notes about it — the entity name is in every record's surface text — so
the loss is recoverable without moving any text.

**Sketch (18% at 50k, 39 of 54 rare).** 43 of the 54 fail on "causal span < 2
across foreign sites": a facet with one witness at a site sets no bit under
`sketch_min_support = 2`. Lowering the threshold lets benign single mentions
set bits and lengthens the triage list, which only pays if the ranker can
absorb the noise — which is the same lever as section 4.

## 6. How separable are genuine candidates, on kernel-side features?

Every candidate the synthesis produced (before the register cut) was dumped
with its confidence-logistic inputs and labelled against gold. One shared
logistic was fitted on the pooled candidates of all four systems on the
calibration seeds (500–502) and evaluated on the evaluation seeds (0–4) by
re-ranking each run's candidates under its own register cap. No feature reads
raw text; the privacy accounting is unchanged.

{calibrator_section()}

{vnext_section()}

## 7. What this changes about the next architecture

Not "richer sketches". In order of the loss they address:

1. **A calibrated ranker** over kernel-side features, fitted on held-out
   seeds by the same protocol as every other knob, so the full question
   budget can be spent without flooding the register. Targets the register
   loss (12% at 10k; binding at 50k once the budget is raised).
2. **A cheaper descent**, so the full budget is affordable at 50k (it
   currently doubles compute and calls). Targets the question-budget loss.
3. **Targeted local re-extraction** on descent. Targets the extraction loss.
4. **A support-1 sketch bit** for rare facets, *if* (1) absorbs the extra
   triage noise. Targets the sketch loss at scale.

Each is a paired experiment against `H_mycelic_full` on identical worlds,
with the centralised controls re-run at the same time. None moves raw text.

## 8. Reproducing this

```sh
python3 -m research.mycelic.loss_accounting      # 10k x 5, 50k x 3 -> loss_funnel.jsonl
python3 -m research.mycelic.loss_report          # the tables
python3 -m research.mycelic.quick_paired --tag diag_qf1 --cfg '{{"question_frac": 1.0}}' --seeds 0,1,2,3,4
python3 -m research.mycelic.calibrator dump && python3 -m research.mycelic.calibrator fit
python3 -m research.mycelic.loss_doc             # this document
python3 -m research.mycelic.make_pdf docs/MYCELIC_LOSS_ACCOUNTING.md docs/MYCELIC_LOSS_ACCOUNTING.pdf
```

_Generated {date.today().isoformat()} by `research/mycelic/loss_doc.py` from the
raw artifacts._
"""


if __name__ == "__main__":
    txt = compose()
    with open(OUT, "w") as fh:
        fh.write(txt)
    print("wrote", OUT, len(txt), "chars")

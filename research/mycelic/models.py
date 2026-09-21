"""Model tiers as explicit, sweepable capability vectors.

IMPORTANT / honesty note
------------------------
The numbers in ``ANCHORS`` below are *assumed placements* of named model
classes on a capability axis, except where a field is marked ``measured``
after a calibration run (see ``calibration.py`` / ``artifacts/calibration*``).
Nothing here is a measurement of Qwen2.5-7B or any other specific checkpoint;
this environment has no GPU and no local inference.  What the benchmark
actually establishes is a *function* from operator quality to end-to-end
architecture quality.  Named models are labels on that axis.

The primary independent variable is therefore the scalar ``q`` in [0, 1].
``tier_from_q`` gives a monotone capability vector; the named anchors are
points on the same axis so that sweeps and named configurations are
commensurable.

Cost is reported primarily in *normalised compute units*:
    cu = active_params_B * tokens / 1e3
which is transparent and provider-independent.  A dollar estimate is offered
as a clearly-labelled secondary column using the price assumptions in
``USD_PER_MTOK``.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from typing import Dict, List, Optional

import numpy as np


@dataclass(frozen=True)
class Tier:
    name: str
    q: float                 # position on the capability axis
    params_b: float          # nominal *active* params, billions (cost proxy)
    ctx: int                 # usable context tokens

    # --- extraction (text -> structured claim) ---
    extract_recall: float
    extract_precision: float
    pred_confusion: float    # P(predicate mapped to a schema neighbour)
    entity_fidelity: float   # P(anchor entity resolved exactly)

    # --- abstraction (claims -> knowledge objects for the parent) ---
    salience_noise: float    # sigma of noise on importance ranking
    abstract_retention: float
    abstract_distortion: float

    # --- synthesis / verification ---
    synth_depth: int         # max facets jointly reasoned over
    causal_check: float
    temporal_check: float
    entity_check: float
    dedup_check: float
    contradiction_acc: float
    hallucination: float     # P(unsupported hypothesis per synthesis call)

    # --- questioning / routing ---
    question_quality: float
    route_quality: float

    # --- runtime ---
    prefill_tok_s: float
    decode_tok_s: float
    concurrency: int         # concurrent calls the fleet can run at this tier

    def cu(self, tok_in: int, tok_out: int) -> float:
        """normalised compute units"""
        return self.params_b * (tok_in + 4.0 * tok_out) / 1e3

    def seconds(self, tok_in: int, tok_out: int) -> float:
        return tok_in / self.prefill_tok_s + tok_out / self.decode_tok_s


# Price assumptions (USD per million tokens) used ONLY for the secondary
# dollar column.  Self-hosted open-weight rates are rough GPU-amortised
# estimates; the frontier rate is an API list-price order of magnitude.
USD_PER_MTOK: Dict[str, float] = {
    "edge-3b": 0.04, "small-7b": 0.08, "mid-14b": 0.16, "mid-32b": 0.35,
    "large-70b": 0.75, "frontier": 6.00, "frontier-plus": 15.00,
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# How "hard" capabilities (multi-step causal verification, near-miss entity
# discrimination, question generation) scale with q.  This is the single most
# load-bearing modelling assumption in the whole study, so it is a swept
# parameter, not a constant.  Every headline claim is re-run under all three.
HARD_SHAPE = "convex"        # "convex" | "linear" | "concave"


def _hard(q: float, a: float, shape: Optional[str] = None) -> float:
    s = shape or HARD_SHAPE
    if s == "convex":
        return q ** a
    if s == "linear":
        return q
    return 1.0 - (1.0 - q) ** a


def tier_from_q(q: float, name: Optional[str] = None,
                params_b: Optional[float] = None,
                ctx: Optional[int] = None,
                shape: Optional[str] = None) -> Tier:
    """Monotone capability vector as a function of the scalar q in [0,1].

    Two families of shape:
      easy(a) = 1-(1-q)^a   concave - extraction-like skills saturate early
      hard(a)               governed by HARD_SHAPE (convex by default) -
                            multi-step verification skills, where small models
                            are assumed to be disproportionately weak.
    HARD_SHAPE is swept in the sensitivity experiment; if a conclusion only
    holds under "convex" it is reported as shape-dependent.
    """
    q = float(np.clip(q, 0.0, 1.0))
    easy = lambda a: 1.0 - (1.0 - q) ** a          # noqa: E731
    hard = lambda a: _hard(q, a, shape)            # noqa: E731
    dec = lambda hi, lo: _lerp(hi, lo, q)          # noqa: E731
    return Tier(
        name=name or f"q{q:.2f}",
        q=q,
        params_b=params_b if params_b is not None else float(3.0 * (120.0 / 3.0) ** q),
        ctx=ctx if ctx is not None else int(8_000 * (128.0) ** q),
        extract_recall=_lerp(0.55, 0.96, easy(1.4)),
        extract_precision=_lerp(0.70, 0.985, easy(1.6)),
        pred_confusion=dec(0.30, 0.03),
        entity_fidelity=_lerp(0.72, 0.995, easy(1.8)),
        salience_noise=dec(0.42, 0.06),
        abstract_retention=_lerp(0.62, 0.97, easy(1.5)),
        abstract_distortion=dec(0.22, 0.015),
        synth_depth=int(round(_lerp(2.0, 7.0, hard(1.0)))),
        causal_check=_lerp(0.50, 0.97, hard(1.3)),
        temporal_check=_lerp(0.58, 0.99, easy(1.2)),
        entity_check=_lerp(0.48, 0.96, hard(1.2)),
        dedup_check=_lerp(0.55, 0.97, hard(1.0)),
        contradiction_acc=_lerp(0.50, 0.95, hard(1.2)),
        hallucination=_lerp(0.34, 0.025, easy(1.2)),
        question_quality=_lerp(0.30, 0.94, hard(1.4)),
        route_quality=_lerp(0.42, 0.96, hard(1.0)),
        prefill_tok_s=_lerp(9000.0, 1600.0, q),
        decode_tok_s=_lerp(220.0, 42.0, q),
        concurrency=int(round(_lerp(4096, 48, q))),
    )


# Named anchors: positions on the q axis for common model classes.
# ASSUMED unless a calibration artifact overrides them.
ANCHOR_Q: Dict[str, float] = {
    "edge-3b": 0.10,
    "small-7b": 0.28,     # Qwen2.5-7B class
    "mid-14b": 0.44,
    "mid-32b": 0.58,
    "large-70b": 0.72,
    "frontier": 0.88,
    "frontier-plus": 1.00,
}
ANCHOR_PARAMS: Dict[str, float] = {
    "edge-3b": 3.0, "small-7b": 7.0, "mid-14b": 14.0, "mid-32b": 32.0,
    "large-70b": 70.0, "frontier": 200.0, "frontier-plus": 400.0,
}
ANCHOR_CTX: Dict[str, int] = {
    "edge-3b": 16_000, "small-7b": 32_000, "mid-14b": 64_000,
    "mid-32b": 128_000, "large-70b": 200_000, "frontier": 400_000,
    "frontier-plus": 1_000_000,
}


def anchor(name: str) -> Tier:
    return tier_from_q(ANCHOR_Q[name], name=name, params_b=ANCHOR_PARAMS[name],
                       ctx=ANCHOR_CTX[name])


ANCHORS: Dict[str, Tier] = {k: anchor(k) for k in ANCHOR_Q}


# ---------------------------------------------------------------------------
# Level -> tier assignments (the "compute allocation" designs under test)
# ---------------------------------------------------------------------------
# index 0..5 == USER, TEAM, DEPT, SITE, REGION, ENTERPRISE

ALLOCATIONS: Dict[str, List[str]] = {
    # every layer the same small model
    "flat-small":        ["small-7b"] * 6,
    "flat-mid":          ["mid-32b"] * 6,
    "flat-frontier":     ["frontier"] * 6,
    # the "neat" progression the brief warns against
    "naive-ladder":      ["small-7b", "mid-14b", "mid-32b", "large-70b",
                          "frontier", "frontier-plus"],
    # progressive but back-loaded: cheap until the top
    "back-loaded":       ["small-7b", "small-7b", "mid-14b", "mid-32b",
                          "frontier", "frontier-plus"],
    # front-loaded: pay at the bottom where the raw text is
    "front-loaded":      ["mid-14b", "mid-32b", "mid-32b", "mid-14b",
                          "mid-14b", "large-70b"],
    # top-heavy: cheapest possible everywhere except the kernel
    "kernel-only":       ["edge-3b", "edge-3b", "edge-3b", "edge-3b",
                          "edge-3b", "frontier-plus"],
    # two-step: cheap bottom, one strong mid layer, strong kernel
    "two-step":          ["small-7b", "small-7b", "mid-32b", "mid-32b",
                          "mid-32b", "frontier-plus"],
    "edge-bottom":       ["edge-3b", "mid-14b", "mid-32b", "large-70b",
                          "frontier", "frontier-plus"],
}


def allocation(name: str) -> List[Tier]:
    return [ANCHORS[t] for t in ALLOCATIONS[name]]


def uniform_alloc(tier_name: str) -> List[Tier]:
    return [ANCHORS[tier_name]] * 6


def describe(alloc: List[Tier]) -> str:
    return " -> ".join(t.name for t in alloc)


if __name__ == "__main__":
    import json
    for n, t in ANCHORS.items():
        d = asdict(t)
        print(f"{n:14s} q={t.q:.2f} recall={t.extract_recall:.2f} "
              f"causal={t.causal_check:.2f} depth={t.synth_depth} "
              f"halluc={t.hallucination:.3f} ctx={t.ctx}")

"""World generator: organisation, workforce, hidden effects, interactions.

Everything here is *ground truth*.  Honesty rule 1.1: no aggregator, agent or
baseline module may import `Effect`/`GroundTruth` or receive a `World`
object's truth fields; they receive `World.records()` views (raw records) or
sketches derived from observed labels.  `tests/test_no_cheating.py` greps for
violations.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import norm

from .vocab import (
    ATTRIBUTES, ATTR_INDEX, LABELS, LABEL_INDEX, N_ATTR, N_LABELS, N_VALUES, SENSITIVE_KINDS,
    VALUES, CANARY_PREFIX, cell_index, mask_matrix,
)

WORKER_TYPES: tuple[str, ...] = (
    "expert", "average", "weak", "overconfident", "underconfident", "noisy", "duplicator", "adversarial",
)
WORKER_TYPE_INDEX = {t: i for i, t in enumerate(WORKER_TYPES)}
LAYER_NAMES_5 = ("worker", "team", "department", "region", "executive")


# --------------------------------------------------------------------------
# Organisation
# --------------------------------------------------------------------------
@dataclass
class Org:
    n_workers: int
    n_teams: int
    n_departments: int
    n_regions: int
    worker_team: np.ndarray
    team_department: np.ndarray
    department_region: np.ndarray
    worker_type: np.ndarray          # int codes into WORKER_TYPES
    worker_attack: np.ndarray        # int codes; 0 = benign (filled by attacks module)
    team_mix: np.ndarray             # [n_teams, N_ATTR, max_vals] sampling distribution per attribute
    team_bias: np.ndarray            # [n_teams, N_LABELS] additive false-positive bias (correlated teams)
    team_ids: list[str]
    department_ids: list[str]
    region_ids: list[str]

    @property
    def worker_department(self) -> np.ndarray:
        return self.team_department[self.worker_team]

    @property
    def worker_region(self) -> np.ndarray:
        return self.department_region[self.worker_department]

    def unit_ids(self, layer: str) -> list[str]:
        return {
            "worker": [f"W{i:06d}" for i in range(self.n_workers)],
            "team": self.team_ids, "department": self.department_ids,
            "region": self.region_ids, "executive": ["EXEC"],
        }[layer]

    def membership(self, layer: str) -> np.ndarray:
        """Unit index at `layer` for every worker."""
        return {
            "worker": np.arange(self.n_workers),
            "team": self.worker_team,
            "department": self.worker_department,
            "region": self.worker_region,
            "executive": np.zeros(self.n_workers, dtype=np.int64),
        }[layer]

    def n_units(self, layer: str) -> int:
        return {"worker": self.n_workers, "team": self.n_teams, "department": self.n_departments,
                "region": self.n_regions, "executive": 1}[layer]


def build_org(cfg: dict[str, Any], rng: np.random.Generator) -> Org:
    o = cfg["org"]
    n_workers = int(o["n_workers"])
    wpt = int(o["workers_per_team"])
    tpd = int(o["teams_per_department"])
    dpr = int(o["departments_per_region"])
    n_teams = max(1, math.ceil(n_workers / wpt))
    n_departments = max(1, math.ceil(n_teams / tpd))
    n_regions = int(o.get("n_regions") or 0) or max(1, math.ceil(n_departments / dpr))
    worker_team = np.arange(n_workers) // wpt
    team_department = np.arange(n_teams) // tpd
    department_region = np.minimum(np.arange(n_departments) // dpr, n_regions - 1)

    # worker types
    mix = o["worker_mix"]
    probs = np.array([mix.get(t, 0.0) for t in WORKER_TYPES], dtype=float)
    probs = probs / probs.sum()
    worker_type = rng.choice(len(WORKER_TYPES), size=n_workers, p=probs)

    # specialisation mixtures: department centre -> team draw
    alpha = float(o.get("specialization_alpha", 0.35))
    max_vals = int(N_VALUES.max())
    team_mix = np.zeros((n_teams, N_ATTR, max_vals), dtype=float)
    lang_bias = float(o.get("regional_language_bias", 0.6))
    for ai, attr in enumerate(ATTRIBUTES):
        nv = int(N_VALUES[ai])
        if attr == "model_version":
            team_mix[:, ai, :nv] = 1.0 / nv  # overridden by release schedule at sampling time
            continue
        if attr in ("task_family", "input_format", "model_family", "context_len", "tool", "domain"):
            dept_centre = rng.dirichlet(np.full(nv, alpha * 2.5), size=n_departments)
            for t in range(n_teams):
                centre = dept_centre[team_department[t]]
                team_mix[t, ai, :nv] = rng.dirichlet(np.maximum(centre * nv * alpha * 4, 0.05))
        elif attr == "language":
            for t in range(n_teams):
                r = department_region[team_department[t]]
                p = np.full(nv, (1 - lang_bias) / (nv - 1))
                p[r % nv] = lang_bias
                team_mix[t, ai, :nv] = p
        elif attr == "difficulty":
            team_mix[:, ai, :nv] = rng.dirichlet(np.array([3.0, 4.0, 3.0]), size=n_teams)
    # correlated team bias
    team_bias = np.zeros((n_teams, N_LABELS), dtype=float)
    frac = float(o.get("correlated_team_fraction", 0.15))
    strength = float(o.get("team_bias_strength", 0.10))
    biased = rng.random(n_teams) < frac
    for t in np.flatnonzero(biased):
        labs = rng.choice(N_LABELS, size=2, replace=False)
        team_bias[t, labs] = strength
    return Org(
        n_workers=n_workers, n_teams=n_teams, n_departments=n_departments, n_regions=n_regions,
        worker_team=worker_team, team_department=team_department, department_region=department_region,
        worker_type=worker_type, worker_attack=np.zeros(n_workers, dtype=np.int64),
        team_mix=team_mix, team_bias=team_bias,
        team_ids=[f"T{i:04d}" for i in range(n_teams)],
        department_ids=[f"D{i:03d}" for i in range(n_departments)],
        region_ids=[f"R{i:02d}" for i in range(n_regions)],
    )


# --------------------------------------------------------------------------
# Effects (ground truth)
# --------------------------------------------------------------------------
@dataclass
class Effect:
    effect_id: str
    kind: str                       # local | cross_team | global | decoy | contradiction | temporal
    cell: int
    label: int
    delta: float
    sign: int = 1
    valid_from: int = 0
    valid_to: int | None = None     # exclusive round bound; None = open
    regions: tuple[int, ...] | None = None
    group: str | None = None        # contradiction / revision group id
    conditional: bool = False       # contradiction where both sides are true (region-conditional)
    true_side: bool = True          # contradiction: whether this effect is real (delta>0) or a biased artefact
    phase: int = 0                  # temporal: phase index within the revision group
    scope_layer: str | None = None  # None = organisation-wide; else 'team' / 'department' scoped effect
    scope_unit: int = -1
    shape: str | None = None        # contradiction shape: biased_positive | biased_null | conditional
    order: int = 0
    min_layer: str = "unknown"
    n_min: int = 0
    unit_counts: dict[str, int] = field(default_factory=dict)   # max matching count per unit at each layer
    contributing_units: dict[str, int] = field(default_factory=dict)  # units with >= 1 match per layer
    true_independent_support: dict[str, float] = field(default_factory=dict)

    def is_active(self, round_: int, region: int | None = None) -> bool:
        if round_ < self.valid_from or (self.valid_to is not None and round_ >= self.valid_to):
            return False
        if self.regions is not None and region is not None and region not in self.regions:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        ci = cell_index()
        return {
            "effect_id": self.effect_id, "kind": self.kind, "cell": int(self.cell),
            "cell_desc": ci.cell_to_dict(int(self.cell)), "label": LABELS[self.label],
            "delta": self.delta, "sign": self.sign, "valid_from": self.valid_from, "valid_to": self.valid_to,
            "regions": list(self.regions) if self.regions is not None else None, "group": self.group,
            "conditional": self.conditional, "true_side": self.true_side, "phase": self.phase,
            "scope_layer": self.scope_layer, "scope_unit": int(self.scope_unit), "shape": self.shape,
            "order": self.order, "min_layer": self.min_layer, "n_min": self.n_min,
            "unit_counts": self.unit_counts, "contributing_units": self.contributing_units,
            "true_independent_support": self.true_independent_support,
        }


def n_min_for(delta: float, p0: float, alpha: float, power: float, recall: float = 0.82, fpr: float = 0.02) -> int:
    """Sample size for a one-sided two-proportion test of p0 vs p0+delta after
    average-worker label noise (recall, fpr) attenuates both rates."""
    p0o = recall * p0 + fpr * (1 - p0)
    p1o = recall * min(p0 + delta, 0.97) + fpr * (1 - min(p0 + delta, 0.97))
    d = max(p1o - p0o, 1e-3)
    za, zb = norm.ppf(1 - alpha), norm.ppf(power)
    n = ((za * math.sqrt(p0o * (1 - p0o)) + zb * math.sqrt(p1o * (1 - p1o))) / d) ** 2
    return int(math.ceil(max(n, 4)))


# --------------------------------------------------------------------------
# World
# --------------------------------------------------------------------------
@dataclass
class World:
    cfg: dict[str, Any]
    seed: int
    org: Org
    n_rounds: int
    base_rates: np.ndarray               # [families, tasks, labels]
    release_round: dict[str, int]        # version -> round
    effects: list[Effect]
    # interaction arrays (length N)
    attrs: np.ndarray                    # uint8 [N, 9]
    true_labels: np.ndarray              # uint32 mask
    observed_labels: np.ndarray          # uint32 mask (after worker + team noise, before attacks)
    worker: np.ndarray
    round: np.ndarray
    score: np.ndarray
    confidence: np.ndarray
    task_id: np.ndarray                  # int64 task instance id
    fingerprint: np.ndarray              # int64 evidence-root hash (identical for copies)
    origin_worker: np.ndarray
    is_copy: np.ndarray
    sensitive: np.ndarray
    sensitive_kind: np.ndarray           # -1 or index into SENSITIVE_KINDS
    canary: list[str]                    # '' when not sensitive
    mention_mask: np.ndarray             # bool [N, 9]: attributes the worker mentioned in the rationale
    attack_tag: np.ndarray               # int codes (0 benign) filled by attacks module
    injection_payload: list[str]         # '' when none (attacks module)
    p_true: np.ndarray | None = None     # float32 [N, 18] generative label probabilities (evaluation only)
    dataset_sha256: str = ""

    # ---- derived ---------------------------------------------------------
    @property
    def n(self) -> int:
        return len(self.worker)

    @property
    def team(self) -> np.ndarray:
        return self.org.worker_team[self.worker]

    @property
    def department(self) -> np.ndarray:
        return self.org.worker_department[self.worker]

    @property
    def region(self) -> np.ndarray:
        return self.org.worker_region[self.worker]

    def effect_matches(self, e: Effect) -> np.ndarray:
        """Boolean mask of interactions whose attributes satisfy the effect cell (ignores time/region)."""
        return cell_match_mask(self.attrs, e.cell)

    def effect_active_mask(self, e: Effect) -> np.ndarray:
        m = self.effect_matches(e)
        m &= self.round >= e.valid_from
        if e.valid_to is not None:
            m &= self.round < e.valid_to
        if e.regions is not None:
            m &= np.isin(self.region, list(e.regions))
        if e.scope_layer is not None:
            m &= self.org.membership(e.scope_layer)[self.worker] == e.scope_unit
        return m

    def records(self) -> "RecordView":
        """Raw-record view handed to centralized baselines (no truth fields)."""
        return RecordView(self)

    # ---- text rendering (rationale / prompt) -----------------------------
    def rationale(self, i: int) -> str:
        parts = [f"Task {int(self.task_id[i])} evaluated"]
        for ai, attr in enumerate(ATTRIBUTES):
            if self.mention_mask[i, ai]:
                parts.append(f"{attr}={VALUES[attr][int(self.attrs[i, ai])]}")
        labs = [LABELS[l] for l in range(N_LABELS) if int(self.observed_labels[i]) >> l & 1]
        parts.append("errors=" + (",".join(labs) if labs else "none"))
        parts.append(f"score={self.score[i]:.1f}")
        if self.canary[i]:
            parts.append(f"note: {self.canary[i]}")
        if self.injection_payload[i]:
            parts.append(self.injection_payload[i])
        return " | ".join(parts)

    def prompt(self, i: int) -> str:
        a = self.attrs[i]
        s = (f"[{VALUES['task_family'][a[2]]}] {VALUES['domain'][a[5]]} task in {VALUES['language'][a[6]]}, "
             f"{VALUES['context_len'][a[3]]} context, {VALUES['input_format'][a[4]]} input")
        if self.canary[i]:
            s += f" (contains {self.canary[i]})"
        return s

    def model_response(self, i: int) -> str:
        a = self.attrs[i]
        return f"{VALUES['model_family'][a[0]]}-{VALUES['model_version'][a[1]]} response to task {int(self.task_id[i])}"

    def record(self, i: int) -> dict[str, Any]:
        a = self.attrs[i]
        return {
            "worker_id": f"W{int(self.worker[i]):06d}",
            "team_id": self.org.team_ids[int(self.team[i])],
            "department_id": self.org.department_ids[int(self.department[i])],
            "region_id": self.org.region_ids[int(self.region[i])],
            "task_id": int(self.task_id[i]),
            "attributes": {attr: VALUES[attr][int(a[ai])] for ai, attr in enumerate(ATTRIBUTES)},
            "prompt": self.prompt(i),
            "model_response": self.model_response(i),
            "worker_score": float(self.score[i]),
            "error_labels": [LABELS[l] for l in range(N_LABELS) if int(self.observed_labels[i]) >> l & 1],
            "rationale": self.rationale(i),
            "confidence": float(self.confidence[i]),
            "timestamp": f"2026-01-{1 + int(self.round[i]) % 28:02d}T{int(self.task_id[i]) % 24:02d}:00:00Z",
            "round": int(self.round[i]),
            "sensitive": bool(self.sensitive[i]),
        }

    def truth_at(self, e: Effect, round_: int) -> bool:
        return e.is_active(round_)


class RecordView:
    """What centralized systems receive: every raw record, exact attributes,
    observed labels, text.  No truth fields (true_labels, effects) are exposed."""

    def __init__(self, world: World) -> None:
        self.attrs = world.attrs
        self.observed_labels = world.observed_labels
        self.worker = world.worker
        self.team = world.team
        self.department = world.department
        self.region = world.region
        self.round = world.round
        self.score = world.score
        self.confidence = world.confidence
        self.task_id = world.task_id
        self.fingerprint = world.fingerprint
        self.mention_mask = world.mention_mask
        self.n = world.n
        self._world = world
        self.attack_tag = world.attack_tag  # NOTE: not truth about effects; used only by attack simulators to mutate records

    def rationale(self, i: int) -> str:
        return self._world.rationale(i)

    def canaries_in(self, idx: np.ndarray) -> list[str]:
        return [self._world.canary[i] for i in idx if self._world.canary[i]]


def cell_match_mask(attrs: np.ndarray, cell: int) -> np.ndarray:
    ci = cell_index()
    m = np.ones(len(attrs), dtype=bool)
    for a, v in ci.decode(int(cell)):
        m &= attrs[:, a] == v
    return m


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def _sample_attrs(org: Org, worker: np.ndarray, rounds: np.ndarray, release_round: dict[str, int],
                  rng: np.random.Generator) -> np.ndarray:
    n = len(worker)
    attrs = np.zeros((n, N_ATTR), dtype=np.uint8)
    team = org.worker_team[worker]
    for ai, attr in enumerate(ATTRIBUTES):
        nv = int(N_VALUES[ai])
        if attr == "model_version":
            continue
        probs = org.team_mix[team, ai, :nv]           # [n, nv]
        cum = np.cumsum(probs, axis=1)
        u = rng.random(n)[:, None]
        attrs[:, ai] = np.minimum((u > cum).sum(axis=1), nv - 1)
    # model_version follows the release schedule: newest available dominates
    vi = ATTR_INDEX["model_version"]
    n_versions = int(N_VALUES[vi])
    avail = np.ones(n, dtype=np.int64)  # number of versions available at the round
    for v, r in release_round.items():
        avail += (rounds >= r).astype(np.int64)
    avail = np.minimum(avail, n_versions)
    u = rng.random(n)
    # P(newest)=0.6, P(previous)=0.3, rest uniform
    version = np.where(u < 0.6, avail - 1, np.where(u < 0.9, np.maximum(avail - 2, 0),
                       (rng.integers(0, n_versions, size=n) % np.maximum(avail, 1))))
    attrs[:, vi] = version.astype(np.uint8)
    return attrs


def _unit_counts_for_cell(attrs: np.ndarray, cell: int, org: Org, worker: np.ndarray, valid: np.ndarray | None = None) -> dict[str, tuple[int, int]]:
    """For each layer: (max matching count in any unit, number of units with >=1 match)."""
    m = cell_match_mask(attrs, cell)
    if valid is not None:
        m &= valid
    out = {}
    for layer in LAYER_NAMES_5:
        mem = org.membership(layer)[worker[m]]
        counts = np.bincount(mem, minlength=org.n_units(layer))
        out[layer] = (int(counts.max()) if len(counts) else 0, int((counts > 0).sum()))
    return out


def _true_independent_support(attrs: np.ndarray, cell: int, org: Org, worker: np.ndarray, is_copy: np.ndarray,
                              worker_attack: np.ndarray, rho: tuple[float, float, float],
                              valid: np.ndarray | None = None) -> dict[str, float]:
    """Ground-truth independent support of an effect at each layer using
    honest, non-copied contributing workers (the same discount formula the
    lineage-aware aggregator applies to its *estimated* sources)."""
    m = cell_match_mask(attrs, cell) & ~is_copy & (worker_attack[worker] == 0)
    if valid is not None:
        m &= valid
    w = np.unique(worker[m])
    rho_t, rho_d, rho_r = rho
    out = {}
    for layer in LAYER_NAMES_5:
        mem_layer = org.membership(layer)
        best = 0.0
        for u in np.unique(mem_layer[w]):
            ws = w[mem_layer[w] == u]
            dw = len(ws)
            dt = len(np.unique(org.worker_team[ws]))
            dd = len(np.unique(org.worker_department[ws]))
            dr = len(np.unique(org.worker_region[ws]))
            is_ = dr + rho_r * (dd - dr) + rho_d * (dt - dd) + rho_t * (dw - dt)
            best = max(best, is_)
        out[layer] = round(float(best), 3)
    return out


def _sample_cell(rng: np.random.Generator, order: int, require_model: bool = True) -> int:
    ci = cell_index()
    attrs_avail = list(range(N_ATTR))
    chosen: list[int] = []
    if require_model and rng.random() < 0.8:
        chosen.append(ATTR_INDEX["model_family"])
    # avoid model_version in most cells (temporal effects add it explicitly)
    pool = [a for a in attrs_avail if a not in chosen and a != ATTR_INDEX["model_version"]]
    extra = rng.choice(pool, size=order - len(chosen), replace=False)
    chosen.extend(int(a) for a in extra)
    return ci.encode([(a, int(rng.integers(0, N_VALUES[a]))) for a in chosen])


def _is_sub_or_super(ci, c1: int, c2: int) -> bool:
    s1, s2 = set(ci.decode(c1)), set(ci.decode(c2))
    return s1 <= s2 or s2 <= s1


def generate_world(cfg: dict[str, Any], seed: int) -> World:
    rng = np.random.default_rng(seed)
    o, w = cfg["org"], cfg["world"]
    ci = cell_index()
    org = build_org(cfg, rng)
    n_rounds = int(o["n_rounds"])
    ipw = int(o["interactions_per_worker"])
    n = org.n_workers * ipw
    release_round = {v: int(round(f * n_rounds)) for v, f in w.get("version_release_rounds", {}).items()}

    # interactions: worker, round, task ids
    worker = np.repeat(np.arange(org.n_workers), ipw)
    rounds = rng.integers(0, n_rounds, size=n)
    order_ = np.lexsort((worker, rounds))
    worker, rounds = worker[order_], rounds[order_]
    task_id = np.arange(n, dtype=np.int64) + 100000
    attrs = _sample_attrs(org, worker, rounds, release_round, rng)

    # base rates: per label only.  Model/task-specific baseline differences are
    # explicit ground-truth effects of kind "base_rate" (order 1) so that every
    # real association in the world is enumerable.
    nf, nt = int(N_VALUES[0]), int(N_VALUES[2])
    base_l = np.clip(rng.normal(w["base_rate_mean"], w["base_rate_spread"], size=N_LABELS), 0.005, 0.20)
    base = np.broadcast_to(base_l[None, None, :], (nf, nt, N_LABELS)).copy()

    # worker noise params
    wt = o["worker_types"]
    recall = np.array([wt[t]["label_recall"] for t in WORKER_TYPES])[org.worker_type]
    fpr = np.array([wt[t]["label_fpr"] for t in WORKER_TYPES])[org.worker_type]
    conf_bias = np.array([wt[t]["conf_bias"] for t in WORKER_TYPES])[org.worker_type]
    conf_noise = np.array([wt[t]["conf_noise"] for t in WORKER_TYPES])[org.worker_type]
    mention = np.array([wt[t]["mention_prob"] for t in WORKER_TYPES])[org.worker_type]
    copy_prob = np.array([wt[t]["copy_prob"] for t in WORKER_TYPES])[org.worker_type]
    avg_recall = float(np.mean(recall)); avg_fpr = float(np.mean(fpr))

    # ---- copies (duplicator behaviour), part 1: attribute rows -------------
    # Decided *before* effects are planted so that effect classification (minimum
    # discovery layer, per-unit evidence counts) sees the final attribute rows;
    # outcome fields are replicated in part 2 once they exist.  Copies are excluded
    # from evidence counts: they are not independent evidence (lineage-aware systems
    # dedup them by fingerprint), so the minimum-layer property holds on unique records.
    is_copy = np.zeros(n, dtype=bool)
    copy_src = np.arange(n)
    team = org.worker_team[worker]
    dup_idx = np.flatnonzero(rng.random(n) < copy_prob[worker])
    if len(dup_idx):
        is_dup = np.zeros(n, dtype=bool); is_dup[dup_idx] = True
        by_team: dict[int, np.ndarray] = {}
        for t in np.unique(team[dup_idx]):
            by_team[int(t)] = np.flatnonzero((team == t) & ~is_dup)   # copy an original teammate record
        for i in dup_idx:
            pool = by_team[int(team[i])]
            pool = pool[(rounds[pool] <= rounds[i]) & (worker[pool] != worker[i])]   # a teammate's earlier record
            if len(pool) == 0:
                continue
            j = int(pool[rng.integers(0, len(pool))])
            attrs[i] = attrs[j]; copy_src[i] = j; is_copy[i] = True
    unique_rec = ~is_copy

    # ---- effects -----------------------------------------------------------
    effects: list[Effect] = []
    eid_seq = itertools.count()   # effect ids stay unique across validation drops and top-ups
    used_cells: list[tuple[int, int]] = []   # (cell, label)
    lo, hi = w["effect_delta_range"]
    alpha, power = float(w["power_alpha"]), float(w["power_target"])
    gen_effect_min = float(w.get("generator_effect_min", 0.10))
    team_of = org.worker_team[worker]
    dept_of = org.worker_department[worker]
    region_of = org.worker_region[worker]
    region = region_of
    # the generative label-probability matrix is maintained incrementally so that every
    # candidate is calibrated against the outside rate the shared interaction test will see
    p = base[attrs[:, 0], attrs[:, 2], :].copy()   # [n, labels]

    def effect_rows(cell: int, valid_from: int = 0, valid_to: int | None = None, regions=None,
                    scope_layer: str | None = None, scope_unit: int = -1) -> np.ndarray:
        m = cell_match_mask(attrs, cell) & (rounds >= valid_from)
        if valid_to is not None:
            m &= rounds < valid_to
        if regions is not None:
            m &= np.isin(region, list(regions))
        if scope_layer in ("team", "department", "region"):
            m &= org.membership(scope_layer)[worker] == scope_unit
        return m

    def apply_effect(cell: int, label: int, delta: float, sign: float = 1.0, **kw: Any) -> None:
        if delta == 0.0:
            return
        p[effect_rows(cell, **kw), label] += sign * delta

    def outside_rate(cell: int, label: int, valid: np.ndarray) -> float:
        """Rate of the most elevated order-(k-1) sub-cell outside the cell (what the test contrasts against)."""
        pairs = ci.decode(int(cell))
        cm = cell_match_mask(attrs, cell)
        best = -1.0
        if len(pairs) == 1:
            out = valid & ~cm
            return float(p[out, label].mean()) if out.any() else 0.0
        for drop in range(len(pairs)):
            sub = np.ones(n, dtype=bool)
            for j, (a, v) in enumerate(pairs):
                if j != drop:
                    sub &= attrs[:, a] == v
            out = valid & sub & ~cm
            if out.sum() >= 10:
                best = max(best, float(p[out, label].mean()))
        if best < 0:
            out = valid & ~cm
            best = float(p[out, label].mean()) if out.any() else 0.0
        return best

    def scope_mask(scope_layer: str | None, scope_unit: int) -> np.ndarray | None:
        if scope_layer is None:
            return None
        return org.membership(scope_layer)[worker] == scope_unit

    def classify(cell: int, delta: float, label: int, valid: np.ndarray | None, planted: bool = False) -> tuple[str, int, dict, dict]:
        """Minimum discovery layer of a candidate effect, calibrated to the shared interaction test:
        the outside rate is the most elevated sub-marginal under the *current* probability matrix (other
        planted effects included), the inside rate is the cell's current rate plus delta (or the current
        rate when the effect is already planted), both attenuated by average worker label noise."""
        valid = unique_rec if valid is None else (valid & unique_rec)   # copies are not evidence
        m = cell_match_mask(attrs, cell) & valid
        if m.sum() == 0:
            return "none", 10**9, {}, {}
        p0 = outside_rate(cell, label, valid)
        p1 = float(p[m, label].mean()) + (0.0 if planted else delta)
        delta_eff = min(p1, 0.97) - p0
        if delta_eff < gen_effect_min:
            return "none", 10**9, {}, {}
        nmin = n_min_for(delta_eff, p0, alpha, power, avg_recall, avg_fpr)
        uc = _unit_counts_for_cell(attrs, cell, org, worker, valid)
        margin = float(w.get("power_margin", 1.5))
        min_layer = "none"
        for layer in LAYER_NAMES_5:
            if uc[layer][0] >= nmin * margin:
                min_layer = layer
                break
        return min_layer, nmin, {l: uc[l][0] for l in uc}, {l: uc[l][1] for l in uc}

    def conflicts_existing(cell: int, label: int) -> bool:
        return any(lab == label and _is_sub_or_super(ci, cell, c) for c, lab in used_cells)

    def sample_cell_from(pool_idx: np.ndarray, order: int) -> int:
        """Sample a cell that is guaranteed to have matches in `pool_idx` by
        taking attribute values from one of its interactions."""
        i = int(pool_idx[rng.integers(0, len(pool_idx))])
        chosen: list[int] = []
        if rng.random() < 0.8:
            chosen.append(ATTR_INDEX["model_family"])
        pool = [a for a in range(N_ATTR) if a not in chosen and a != ATTR_INDEX["model_version"]]
        chosen.extend(int(a) for a in rng.choice(pool, size=order - len(chosen), replace=False))
        return ci.encode([(a, int(attrs[i, a])) for a in chosen])

    def add_effects(kind: str, count: int, orders: list[int], allowed_layers: set[str], scope_layer: str | None,
                    extra_check=None) -> int:
        added = 0
        attempts = 0
        max_attempts = int(w.get("max_generation_attempts", 200)) * max(count, 1)
        n_scope_units = org.n_units(scope_layer) if scope_layer else 1
        while added < count and attempts < max_attempts:
            attempts += 1
            order = int(rng.choice(orders))
            scope_unit = int(rng.integers(0, n_scope_units)) if scope_layer else -1
            valid = scope_mask(scope_layer, scope_unit)
            pool_idx = np.flatnonzero(valid) if valid is not None else np.arange(n)
            if len(pool_idx) == 0:
                continue
            cell = sample_cell_from(pool_idx, order)
            label = int(rng.integers(0, N_LABELS))
            if conflicts_existing(cell, label):
                continue
            delta = float(rng.uniform(lo, hi))
            min_layer, nmin, uc, cu = classify(cell, delta, label, valid)
            if min_layer not in allowed_layers:
                continue
            if extra_check is not None and not extra_check(uc, cu, min_layer, nmin):
                continue
            eid = f"{kind[:3].upper()}{next(eid_seq):04d}"
            effects.append(Effect(eid, kind, cell, label, delta, order=order, min_layer=min_layer, n_min=nmin,
                                  unit_counts=uc, contributing_units=cu, scope_layer=scope_layer, scope_unit=scope_unit))
            apply_effect(cell, label, delta, scope_layer=scope_layer, scope_unit=scope_unit)
            used_cells.append((cell, label))
            added += 1
        return added

    gen_report: dict[str, Any] = {}
    # base-rate structure: order-1 model/task effects, organisation-wide, modest delta
    n_base = int(w.get("n_base_rate_effects", 12))
    added = 0; attempts = 0
    while added < n_base and attempts < 40 * max(n_base, 1):
        attempts += 1
        a = int(rng.choice([ATTR_INDEX["model_family"], ATTR_INDEX["task_family"]]))
        cell = ci.encode([(a, int(rng.integers(0, N_VALUES[a])))])
        label = int(rng.integers(0, N_LABELS))
        if conflicts_existing(cell, label):
            continue
        delta = float(rng.uniform(*w.get("base_rate_effect_range", [0.04, 0.12])))
        min_layer, nmin, uc, cu = classify(cell, delta, label, None)
        if min_layer == "none":
            continue
        effects.append(Effect(f"BAS{next(eid_seq):04d}", "base_rate", cell, label, delta, order=1, min_layer=min_layer, n_min=nmin,
                              unit_counts=uc, contributing_units=cu))
        apply_effect(cell, label, delta)
        used_cells.append((cell, label)); added += 1
    gen_report["base_rate"] = added
    # organisation-wide effects (contradictions, temporal phases) are planted BEFORE scoped effects so that
    # they cannot mask them; a scoped effect changes an organisation-wide sub-marginal only marginally.
    # contradictions.  Three shapes:
    #  (a) biased_positive: no true effect; 1-2 correlated teams in one department over-report the label on the
    #      cell (their positive claim is false; correct outcome = reject it);
    #  (b) conditional: +delta only in a subset of regions (both sides true; correct = split by region / keep open);
    #  (c) biased_null: true effect; 1-2 correlated teams under-report it (correct = accept the positive side).
    n_con = 0
    attempts = 0
    contradiction_bias_teams: dict[str, tuple[str, list[int]]] = {}
    n_con_target = int(round(int(w["n_contradictions"]) * float(w.get('overgenerate_factor', 1.4))))
    while n_con < n_con_target and attempts < 40 * n_con_target:
        attempts += 1
        order = int(rng.choice([2, 3]))
        cell = sample_cell_from(np.arange(n), order)
        label = int(rng.integers(0, N_LABELS))
        if conflicts_existing(cell, label):
            continue
        delta = float(rng.uniform(lo, hi))
        min_layer, nmin, uc, cu = classify(cell, delta, label, None)
        if min_layer in ("none", "worker"):
            continue
        gid = f"CG{n_con:03d}"
        shape = rng.choice(["biased_positive", "conditional", "biased_null"], p=[0.45, 0.30, 0.25]) if org.n_regions >= 2 \
            else rng.choice(["biased_positive", "biased_null"], p=[0.6, 0.4])
        m = cell_match_mask(attrs, cell)
        if shape == "conditional":
            regs = rng.permutation(org.n_regions)
            half = max(1, org.n_regions // 2)
            a_regs, b_regs = tuple(int(r) for r in regs[:half]), tuple(int(r) for r in regs[half:])
            if not b_regs:
                continue
            if (np.isin(region_of[m], a_regs).sum() < nmin) or (np.isin(region_of[m], b_regs).sum() < nmin):
                continue
            effects.append(Effect(f"CON{next(eid_seq):04d}", "contradiction", cell, label, delta, sign=1, regions=a_regs,
                                  group=gid, conditional=True, true_side=True, order=order, min_layer=min_layer, n_min=nmin,
                                  unit_counts=uc, contributing_units=cu))
            apply_effect(cell, label, delta, regions=a_regs)
            used_cells.append((cell, label))
            effects.append(Effect(f"CON{next(eid_seq):04d}", "contradiction", cell, label, 0.0, sign=-1, regions=b_regs,
                                  group=gid, conditional=True, true_side=True, order=order, min_layer=min_layer, n_min=nmin,
                                  unit_counts=uc, contributing_units=cu))
        else:
            teams_with, tcounts = np.unique(team_of[m & unique_rec], return_counts=True)
            if len(teams_with) < 3:
                continue
            depts = org.team_department[teams_with]
            d = int(rng.choice(depts))
            cand = teams_with[depts == d]
            if len(cand) < 1 or len(cand) >= len(teams_with) - 1:
                continue
            # the biased team(s) must hold enough matching records to surface the (false or suppressed)
            # claim at their own layer, and the honest remainder of the organisation must hold enough
            # records to contradict it (otherwise no system could tell the two apart)
            cand_counts = tcounts[np.isin(teams_with, cand)]
            enough = cand[cand_counts >= nmin * float(w.get("power_margin", 1.5))]
            if len(enough) == 0:
                continue
            k = min(len(enough), int(rng.integers(1, 3)))
            bias_teams = [int(t) for t in rng.choice(enough, size=k, replace=False)]
            honest = int(tcounts[~np.isin(teams_with, bias_teams)].sum())
            if honest < 2 * nmin:
                continue
            if shape == "biased_null":
                effects.append(Effect(f"CON{next(eid_seq):04d}", "contradiction", cell, label, delta, sign=1,
                                      group=gid, conditional=False, true_side=True, order=order, min_layer=min_layer, n_min=nmin,
                                      unit_counts=uc, contributing_units=cu))
                apply_effect(cell, label, delta)
                used_cells.append((cell, label))
                effects.append(Effect(f"CON{next(eid_seq):04d}", "contradiction", cell, label, 0.0, sign=-1,
                                      group=gid, conditional=False, true_side=False, order=order, min_layer=min_layer, n_min=nmin,
                                      unit_counts={"biased_teams": len(bias_teams)}, contributing_units={},
                                      scope_layer="team_set", scope_unit=-1))
            else:  # biased_positive: the positive side is the false one
                effects.append(Effect(f"CON{next(eid_seq):04d}", "contradiction", cell, label, delta, sign=1,
                                      group=gid, conditional=False, true_side=False, order=order, min_layer=min_layer, n_min=nmin,
                                      unit_counts={"biased_teams": len(bias_teams)}, contributing_units={"bias_teams": bias_teams},
                                      scope_layer="team_set", scope_unit=-1))
                used_cells.append((cell, label))
                effects.append(Effect(f"CON{next(eid_seq):04d}", "contradiction", cell, label, 0.0, sign=-1,
                                      group=gid, conditional=False, true_side=True, order=order, min_layer=min_layer, n_min=nmin,
                                      unit_counts=uc, contributing_units=cu))
            contradiction_bias_teams[gid] = (shape, bias_teams)
        for e in effects:
            if e.group == gid:
                e.shape = str(shape)
        n_con += 1
    gen_report["contradiction"] = n_con

    # temporal revision groups: phases over rounds (delta on / off / on)
    n_tmp = 0
    attempts = 0
    release_rounds_sorted = sorted(release_round.values())
    n_tmp_target = int(round(int(w["n_temporal_revisions"]) * float(w.get('overgenerate_factor', 1.4)) * 1.5))
    while n_tmp < n_tmp_target and attempts < 40 * n_tmp_target:
        attempts += 1
        order = int(rng.choice([2, 3]))
        cell = sample_cell_from(np.arange(n), order)
        label = int(rng.integers(0, N_LABELS))
        if conflicts_existing(cell, label):
            continue
        delta = float(rng.uniform(lo, hi))
        # each phase must be individually detectable somewhere in the organisation
        cuts = [0] + release_rounds_sorted + [n_rounds]
        ok = True
        phase_layers = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            ml, nmin, uc, cu = classify(cell, delta, label, (rounds >= a) & (rounds < b))
            if ml == "none":
                ok = False
                break
            phase_layers.append((ml, nmin, uc, cu))
        if not ok:
            continue
        gid = f"TG{n_tmp:03d}"
        pattern = [1, 0, 1, 0][: len(cuts) - 1] if rng.random() < 0.5 else [1, 0, 0, 1][: len(cuts) - 1]
        for ph, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
            ml, nmin, uc, cu = phase_layers[ph]
            effects.append(Effect(f"TMP{next(eid_seq):04d}", "temporal", cell, label, delta * pattern[ph], sign=1,
                                  valid_from=a, valid_to=b, group=gid, phase=ph, order=order, min_layer=ml,
                                  n_min=nmin, unit_counts=uc, contributing_units=cu))
            apply_effect(cell, label, delta * pattern[ph], valid_from=a, valid_to=b)
        used_cells.append((cell, label))
        n_tmp += 1
    gen_report["temporal"] = n_tmp

    # local: scoped to one team, discoverable by that team alone
    overgen = float(w.get('overgenerate_factor', 1.4))   # plant extra candidates; trim to the requested counts after validation
    gen_report["local"] = add_effects("local", int(round(int(w["n_local_findings"]) * overgen)), list(w["local_orders"]),
                                      {"worker", "team"}, "team")
    # cross-team: scoped to one department; no team alone has enough evidence, >=2 teams contribute
    hidden_frac = float(w.get("hidden_max_fraction", 0.5))   # no single unit below the min layer may hold more than this x n_min x margin
    hidden_cap = lambda nmin: hidden_frac * nmin * float(w.get("power_margin", 1.5))
    cross_check = lambda uc, cu, ml, nmin: cu["team"] >= 2 and uc["team"] <= hidden_cap(nmin)
    gen_report["cross_team"] = add_effects(
        "cross_team", int(round(int(w["n_cross_team_findings"]) * overgen)), list(w["cross_team_orders"]), {"department"}, "department",
        extra_check=cross_check)
    # global: organisation-wide; evidence spread across departments (and regions when they exist)
    global_layers = {"region", "executive"}
    if org.n_regions == 1:
        global_layers = {"executive"}
    if org.n_departments == 1:
        global_layers = {"department", "executive"}
    if str(w.get("min_layer_for_global", "department")) == "department":
        global_layers |= {"department"}
    global_check = lambda uc, cu, ml, nmin: (cu["department"] >= 2 and cu["team"] >= 3
        and (org.n_regions == 1 or cu["region"] >= 2) and uc["team"] <= hidden_cap(nmin)
        and (ml == "department" or uc["department"] <= hidden_cap(nmin)))
    gen_report["global"] = add_effects(
        "global", int(round(int(w["n_global_findings"]) * overgen)), list(w["global_orders"]), global_layers, None,
        extra_check=global_check)
    # decoys: zero-delta cells adjacent to real effects (share label and 2 attribute-values)
    n_dec = 0
    real = [e for e in effects if e.kind != "base_rate"]
    attempts = 0
    while n_dec < int(w["n_decoys"]) and real and attempts < 20 * int(w["n_decoys"]):
        attempts += 1
        e = real[int(rng.integers(0, len(real)))]
        pairs = list(ci.decode(e.cell))
        if len(pairs) < 2:
            continue
        keep = [pairs[i] for i in sorted(rng.choice(len(pairs), size=min(2, len(pairs)), replace=False))]
        others = [a for a in range(N_ATTR) if a not in {a for a, _ in keep} and a != ATTR_INDEX["model_version"]]
        a_new = int(rng.choice(others))
        cell = ci.encode(keep + [(a_new, int(rng.integers(0, N_VALUES[a_new])))])
        if conflicts_existing(cell, e.label):
            continue
        effects.append(Effect(f"DEC{next(eid_seq):04d}", "decoy", cell, e.label, 0.0, order=len(keep) + 1, min_layer="none"))
        used_cells.append((cell, e.label))
        n_dec += 1
    gen_report["decoy"] = n_dec

    # ---- validation pass: every planted effect must still meet its kind's layer requirement under the
    # final probability matrix (later effects can raise a sub-marginal and mask an earlier one).  Effects
    # that fail are removed (their delta subtracted) and counted; two passes converge in practice.
    def kind_requirement(e: Effect, ml: str, uc: dict, cu: dict) -> bool:
        if e.kind == "local":
            return ml in ("worker", "team")
        if e.kind == "cross_team":
            return ml == "department" and cu.get("team", 0) >= 2 and uc.get("team", 0) <= hidden_cap(e.n_min)
        if e.kind == "global":
            return ml in global_layers and cu.get("department", 0) >= 2 and cu.get("team", 0) >= 3 \
                and (org.n_regions == 1 or cu.get("region", 0) >= 2) and uc.get("team", 0) <= hidden_cap(e.n_min) \
                and (ml == "department" or uc.get("department", 0) <= hidden_cap(e.n_min))
        if e.kind == "temporal":
            return ml != "none"
        if e.kind == "contradiction":
            return ml not in ("none", "worker")
        return ml != "none"

    dropped: dict[str, int] = {}

    def validation_pass() -> None:
        """Re-classify every planted effect under the final probability matrix; drop the ones whose kind
        requirement no longer holds (later effects can raise a sub-marginal and mask an earlier one)."""
        nonlocal contradiction_bias_teams
        for _pass in range(2):
            drop_groups: set[str] = set(); drop_ids: set[str] = set()
            for e in effects:
                if e.delta <= 0 or not e.true_side or e.kind in ("base_rate", "decoy"):
                    continue
                valid = None
                if e.scope_layer in ("team", "department", "region"):
                    valid = org.membership(e.scope_layer)[worker] == e.scope_unit
                if e.kind == "temporal":
                    valid = (rounds >= e.valid_from) & (rounds < (e.valid_to if e.valid_to is not None else n_rounds))
                if e.regions is not None:
                    vr = np.isin(region, list(e.regions))
                    valid = vr if valid is None else (valid & vr)
                ml, nmin, uc, cu = classify(e.cell, e.delta, e.label, valid, planted=True)
                if kind_requirement(e, ml, uc, cu):
                    e.min_layer, e.n_min, e.unit_counts, e.contributing_units = ml, nmin, uc, cu
                elif e.group is not None:
                    drop_groups.add(e.group)
                else:
                    drop_ids.add(e.effect_id)
            if not drop_groups and not drop_ids:
                break
            keep: list[Effect] = []
            for e in effects:
                if e.effect_id in drop_ids or (e.group is not None and e.group in drop_groups):
                    dropped[e.kind] = dropped.get(e.kind, 0) + 1
                    if e.delta > 0 and e.true_side:
                        apply_effect(e.cell, e.label, e.delta, sign=-1.0, valid_from=e.valid_from, valid_to=e.valid_to,
                                     regions=e.regions, scope_layer=e.scope_layer, scope_unit=e.scope_unit)
                    used_cells[:] = [uc_ for uc_ in used_cells if uc_ != (e.cell, e.label)]
                else:
                    keep.append(e)
            effects[:] = keep
            contradiction_bias_teams = {g: v for g, v in contradiction_bias_teams.items() if g not in drop_groups}

    validation_pass()
    # top-up: kinds whose validated count fell below the requested count are re-planted (the hidden-evidence
    # cap and masking by later effects drop candidates unevenly across kinds), then validated again
    topup_specs = {"local": (int(w["n_local_findings"]), list(w["local_orders"]), {"worker", "team"}, "team", None),
                   "cross_team": (int(w["n_cross_team_findings"]), list(w["cross_team_orders"]), {"department"}, "department", cross_check),
                   "global": (int(w["n_global_findings"]), list(w["global_orders"]), global_layers, None, global_check)}
    gen_report["topups"] = {}
    for _topup in range(int(w.get("max_topups", 5))):
        short = {k: spec[0] - sum(1 for e in effects if e.kind == k) for k, spec in topup_specs.items()}
        short = {k: v for k, v in short.items() if v > 0}
        if not short:
            break
        for k, v in short.items():
            target, orders, layers, scope, check = topup_specs[k]
            gen_report["topups"][k] = gen_report["topups"].get(k, 0) + add_effects(k, int(math.ceil(v * overgen)), orders, layers, scope, extra_check=check)
        validation_pass()
    gen_report["validation_dropped"] = dropped
    # trim over-generated kinds back to the requested counts (extras are removed like validation failures)
    targets = {"local": int(w["n_local_findings"]), "cross_team": int(w["n_cross_team_findings"]),
               "global": int(w["n_global_findings"])}
    group_targets = {"contradiction": int(w["n_contradictions"]), "temporal": int(w["n_temporal_revisions"])}
    keep_ids: set[str] = set(); keep_groups: set[str] = set(); seen_groups: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for e in effects:
        if e.kind in targets:
            if counts.get(e.kind, 0) < targets[e.kind]:
                keep_ids.add(e.effect_id); counts[e.kind] = counts.get(e.kind, 0) + 1
        elif e.kind in group_targets:
            seen_groups.setdefault(e.kind, [])
            if e.group not in seen_groups[e.kind] and len(seen_groups[e.kind]) < group_targets[e.kind]:
                seen_groups[e.kind].append(e.group)
            if e.group in seen_groups[e.kind]:
                keep_groups.add(e.group)
        else:
            keep_ids.add(e.effect_id)
    trimmed: list[Effect] = []
    for e in effects:
        if e.effect_id in keep_ids or (e.group is not None and e.group in keep_groups):
            trimmed.append(e)
        else:
            if e.delta > 0 and e.true_side:
                apply_effect(e.cell, e.label, e.delta, sign=-1.0, valid_from=e.valid_from, valid_to=e.valid_to,
                             regions=e.regions, scope_layer=e.scope_layer, scope_unit=e.scope_unit)
            used_cells[:] = [uc_ for uc_ in used_cells if uc_ != (e.cell, e.label)]
    effects[:] = trimmed
    contradiction_bias_teams = {g: v for g, v in contradiction_bias_teams.items() if g in keep_groups}
    gen_report["final_counts"] = {k: sum(1 for e in effects if e.kind == k) for k in ("base_rate", "local", "cross_team", "global", "decoy", "contradiction", "temporal")}

    # ---- true labels (p was built incrementally while planting effects) ------
    p = np.clip(p, 0.0, 0.97)
    true_lab = rng.random((n, N_LABELS)) < p

    # ---- observed labels (worker noise + team bias + contradiction bias) ---
    keep = rng.random((n, N_LABELS)) < recall[worker][:, None]
    false_pos = rng.random((n, N_LABELS)) < (fpr[worker][:, None] + org.team_bias[org.worker_team[worker]])
    obs = (true_lab & keep) | (~true_lab & false_pos)
    for gid, (shape, bias_teams) in contradiction_bias_teams.items():
        eff = [e for e in effects if e.group == gid][0]
        m = cell_match_mask(attrs, eff.cell) & np.isin(org.worker_team[worker], bias_teams)
        if shape == "biased_null":
            obs[m, eff.label] = rng.random(int(m.sum())) < (avg_fpr * 0.5)   # biased teams almost never report it
        else:  # biased_positive: over-report the label on this cell
            obs[m, eff.label] |= rng.random(int(m.sum())) < eff.delta
    observed = (obs.astype(np.int64) << np.arange(N_LABELS)).sum(axis=1).astype(np.int64)
    true_mask = (true_lab.astype(np.int64) << np.arange(N_LABELS)).sum(axis=1).astype(np.int64)

    # score & confidence
    n_true = true_lab.sum(axis=1)
    score = np.clip(10 - 2.5 * n_true + rng.normal(0, 0.8, size=n), 0, 10)
    agree = (obs == true_lab).mean(axis=1)
    confidence = np.clip(agree * 0.6 + 0.3 + conf_bias[worker] + rng.normal(0, 1, size=n) * conf_noise[worker], 0.02, 0.99)

    # ---- copies, part 2: replicate the source record's outcome fields ------
    origin_worker = worker.copy()
    fingerprint = np.array([int(hashlib.blake2b(f"{t}|{ob}|{s:.1f}".encode(), digest_size=8).hexdigest(), 16) % (2**62)
                            for t, ob, s in zip(task_id, observed, np.round(score, 1))], dtype=np.int64)
    for i in np.flatnonzero(is_copy):
        j = int(copy_src[i])
        true_mask[i] = true_mask[j]; observed[i] = observed[j]; score[i] = score[j]; task_id[i] = task_id[j]
        fingerprint[i] = fingerprint[j]; origin_worker[i] = origin_worker[j]

    # ---- sensitive content ------------------------------------------------
    sens = rng.random(n) < float(w["sensitive_fraction"])
    sens_kind = np.where(sens, rng.integers(0, len(SENSITIVE_KINDS), size=n), -1)
    canary = ["" for _ in range(n)]
    for i in np.flatnonzero(sens):
        canary[i] = f"{CANARY_PREFIX}{SENSITIVE_KINDS[sens_kind[i]]}-{hashlib.sha1(f'{seed}|{i}'.encode()).hexdigest()[:8]}"
    mention_mask = rng.random((n, N_ATTR)) < mention[worker][:, None]
    mention_mask[:, ATTR_INDEX["model_family"]] |= rng.random(n) < 0.9   # model is almost always mentioned
    mention_mask[:, ATTR_INDEX["task_family"]] |= rng.random(n) < 0.85

    world = World(
        cfg=cfg, seed=seed, org=org, n_rounds=n_rounds, base_rates=base, release_round=release_round,
        effects=effects, attrs=attrs, true_labels=true_mask, observed_labels=observed, worker=worker,
        round=rounds, score=score, confidence=confidence, task_id=task_id, fingerprint=fingerprint,
        origin_worker=origin_worker, is_copy=is_copy, sensitive=sens, sensitive_kind=sens_kind, canary=canary,
        mention_mask=mention_mask, attack_tag=np.zeros(n, dtype=np.int64), injection_payload=["" for _ in range(n)],
        p_true=p.astype(np.float32),
    )
    # ground-truth independent support per effect at each layer (honest, non-copied, benign)
    rho = (float(cfg.get("security", {}).get("rho_team", 0.25)), float(cfg.get("security", {}).get("rho_department", 0.7)),
           float(cfg.get("security", {}).get("rho_region", 0.9)))
    for e in effects:
        if e.kind in ("local", "cross_team", "global", "temporal", "contradiction") and (e.true_side or e.conditional):
            valid = None
            if e.scope_layer in ("team", "department", "region"):
                valid = org.membership(e.scope_layer)[worker] == e.scope_unit
            e.true_independent_support = _true_independent_support(attrs, e.cell, org, worker, is_copy, org.worker_attack, rho, valid)
    world.dataset_sha256 = dataset_hash(world)
    world.cfg = dict(cfg)
    world.cfg["_generation_report"] = gen_report
    return world


def dataset_hash(world: World) -> str:
    h = hashlib.sha256()
    for arr in (world.attrs, world.observed_labels, world.worker, world.round, np.round(world.score, 3)):
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def ground_truth_summary(world: World) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    layers: dict[str, dict[str, int]] = {}
    for e in world.effects:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
        layers.setdefault(e.kind, {})
        layers[e.kind][e.min_layer] = layers[e.kind].get(e.min_layer, 0) + 1
    return {
        "n_interactions": world.n, "n_workers": world.org.n_workers, "n_teams": world.org.n_teams,
        "n_departments": world.org.n_departments, "n_regions": world.org.n_regions, "n_rounds": world.n_rounds,
        "effects_by_kind": kinds, "min_layer_by_kind": layers, "n_sensitive": int(world.sensitive.sum()),
        "n_copies": int(world.is_copy.sum()), "generation_report": world.cfg.get("_generation_report", {}),
        "dataset_sha256": world.dataset_sha256,
        "worker_type_counts": {t: int((world.org.worker_type == i).sum()) for i, t in enumerate(WORKER_TYPES)},
    }

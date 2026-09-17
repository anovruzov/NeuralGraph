"""Centralized and non-hierarchical baselines (DESIGN.md §8, MODULE_SPEC.md Task A).

All baselines receive **every raw record** through `World.records()` (exact
attributes, observed labels, unit membership, text) — strictly more than the
hierarchy ever sees — and all of them call the shared `hypothesis.search`
(or `rate_test`) with the same thresholds as the hierarchy policy.  What
differs is the information flow:

* B0_isolated          - each worker analyses only its own perceived records.
* B1_central_keyword   - everything shipped raw to a centre; an analyst with a
                         per-round *query budget* explores conjunctions by beam
                         search over exact facet counts (org-wide and per unit).
* B2_central_rag       - same centre, but evidence for each hypothesis is the
                         `top_k` records retrieved by hashed bag-of-words cosine
                         similarity; rates are estimated from those k only.
* B3_central_llm_summary - records chunked in arrival order into windows read by
                         a frontier-profile model; per-chunk sketches pooled
                         without lineage; org-wide search only.
* B4_majority_vote     - candidate cells from the keyword analyst; each worker
                         votes from its own data; replica count decides.
* ORACLE_central_stats - exhaustive search on exact, copy-deduplicated sketches
                         at every organisational scope (upper-bound reference).

Devices emit records through the same `agents.make_batches` path as the
hierarchy (so device-side attacks — spoofed ids, replayed evidence, injected
claims — reach the centre exactly as they reach a team node).  Centralized
systems ingest injected bare claims as additional facet evidence with their
claimed counts (they have no lineage to verify them).
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .agents import ObservationBatch, SimulatedSLM, make_batches
from .config import get_profile
from .evaluate import (ClaimRecord, contradiction_metrics, evidence_metrics, failure_reasons_from_probe, match_effects,
                       privacy_metrics, raw_record_bytes, temporal_metrics, transition_fidelity)
from .hierarchy import Policy
from .hypothesis import Candidates, bh_qvalues, null_evidence, rate_test, search
from .sketch import COL_DD, COL_DR, COL_DT, COL_DW, COL_K0, COL_N, N_COLS, Sketch
from .vocab import ATTRIBUTES, LABELS, N_ATTR, N_LABELS, N_VALUES, VALUES, cell_index, mask_matrix
from .world import World

CANARY_RE = re.compile(r"CANARY-[a-z_]+-[0-9a-f]{8}")
TOKENS_PER_RECORD = 180
SCOPE_LAYERS = ("executive", "region", "department", "team")


# --------------------------------------------------------------------------
# Ingestion: what the centre receives each round (after device-side attacks)
# --------------------------------------------------------------------------
@dataclass
class Ingested:
    idx: np.ndarray                      # world record indices (evaluation drill-down only)
    attrs: np.ndarray
    labels: np.ndarray
    worker: np.ndarray
    team: np.ndarray
    dept: np.ndarray
    region: np.ndarray
    fingerprint: np.ndarray
    round: np.ndarray
    confidence: np.ndarray
    injected: list[dict[str, Any]] = field(default_factory=list)   # bare claims from compromised devices
    quotes: int = 0

    def __len__(self) -> int:
        return len(self.idx)


def _concat(parts: list[Ingested]) -> Ingested:
    if not parts:
        z = np.zeros(0, dtype=np.int64)
        return Ingested(z, np.zeros((0, N_ATTR), dtype=np.int64), z, z, z, z, z, z, z, np.zeros(0))
    return Ingested(
        np.concatenate([p.idx for p in parts]), np.concatenate([p.attrs for p in parts]), np.concatenate([p.labels for p in parts]),
        np.concatenate([p.worker for p in parts]), np.concatenate([p.team for p in parts]), np.concatenate([p.dept for p in parts]),
        np.concatenate([p.region for p in parts]), np.concatenate([p.fingerprint for p in parts]), np.concatenate([p.round for p in parts]),
        np.concatenate([p.confidence for p in parts]), [c for p in parts for c in p.injected], sum(p.quotes for p in parts))


def ingest_round(world: World, records, idx: np.ndarray, slm: SimulatedSLM, attack_hook, failure_plan, round_: int) -> Ingested:
    """Emit this round's records from the devices (perception by `slm`, then the
    attack hook and the failure record filter) and collect them centrally."""
    org = world.org
    batches = make_batches(records, idx, records.team, org.n_teams, slm, "none", attack_hook)
    parts: list[Ingested] = []
    for t, b in batches.items():
        if failure_plan is not None and hasattr(failure_plan, "record_filter"):
            b = failure_plan.record_filter(b, slm)
        if len(b) == 0 and not b.injected_claims:
            continue
        w = b.worker.astype(np.int64)
        parts.append(Ingested(
            b.idx, b.attrs.astype(np.int64), b.labels.astype(np.int64), w, org.worker_team[w], org.worker_department[w],
            org.worker_region[w], b.fingerprint.astype(np.int64), np.full(len(b), round_), b.confidence,
            list(b.injected_claims), len(b.quotes)))
    return _concat(parts)


# --------------------------------------------------------------------------
# Claim bookkeeping shared by all baselines
# --------------------------------------------------------------------------
class ClaimBook:
    """Signature -> claim state with first-acceptance rounds and window revision."""

    def __init__(self, policy: Policy, layer: str) -> None:
        self.policy = policy
        self.layer = layer
        self.claims: dict[tuple, dict[str, Any]] = {}

    def upsert(self, scope_layer: str, scope_unit: int, cands: Candidates, round_: int, support_fn=None) -> None:
        for i in range(len(cands)):
            key = (scope_layer, scope_unit, int(cands.cell[i]), int(cands.label[i]), int(cands.sign[i]))
            c = self.claims.get(key)
            n = int(cands.n[i])
            sup = support_fn(cands, i) if support_fn is not None else float(n)
            conf = float((1.0 - cands.q[i]) * (1.0 - math.exp(-sup / max(self.policy.support_min, 1e-6))))
            if c is None:
                c = {"first_round": round_, "status": "accepted", "valid_to": None}
                self.claims[key] = c
            elif c["status"] == "superseded":
                # a signal that returns after supersession is a revision
                c["status"] = "accepted"; c["first_round"] = round_; c["valid_to"] = None
            c.update({"n": n, "k": int(cands.k[i]), "rate": float(cands.rate[i]), "baseline": float(cands.baseline[i]),
                      "effect": float(cands.effect[i]), "p": float(cands.p[i]), "q": float(cands.q[i]), "conf": conf,
                      "support": sup, "replica": n, "last_round": round_,
                      "dw": int(cands.dw[i]), "dt": int(cands.dt[i]), "dd": int(cands.dd[i]), "dr": int(cands.dr[i])})

    def revise(self, recent: Sketch, scope_layer: str, scope_unit: int, round_: int) -> None:
        """Supersede accepted claims that the recent window refutes with power."""
        p = self.policy
        keys = [k for k, c in self.claims.items() if k[0] == scope_layer and k[1] == scope_unit and c["status"] == "accepted" and k[4] > 0]
        if not keys or len(recent.ids) == 0:
            return
        cells = np.array([k[2] for k in keys])
        cnt = recent.lookup(cells)
        n = cnt[:, COL_N]
        k = np.array([cnt[i, COL_K0 + key[3]] for i, key in enumerate(keys)])
        base = np.array([self.claims[key]["baseline"] for key in keys])
        ref = (n >= p.n_min) & null_evidence(n, k, base, p.effect_min)
        for i in np.flatnonzero(ref):
            self.claims[keys[i]]["status"] = "superseded"; self.claims[keys[i]]["valid_to"] = round_

    def records(self, world: World, include_superseded: bool = True) -> list[ClaimRecord]:
        out = []
        for (sl, su, cell, label, sign), c in self.claims.items():
            if c["status"] == "superseded" and not include_superseded:
                continue
            unit_id = _unit_id(world, sl, su)
            out.append(ClaimRecord(cell=cell, label=label, sign=sign, scope_layer=sl, scope_unit=su, layer=self.layer,
                                   round_accepted=c["first_round"], confidence=c["conf"], independent_support=float(c["support"]),
                                   replica_count=int(c["replica"]), contributing_units=[unit_id], status=c["status"],
                                   claim_id=f"{self.layer}:{sl}:{su}:{cell}:{label}:{'+' if sign > 0 else '-'}", valid_to=c["valid_to"]))
        return out


def _unit_id(world: World, layer: str, unit: int) -> str:
    org = world.org
    if layer == "executive":
        return "EXEC"
    if layer == "worker":
        return f"W{unit:06d}"
    return {"team": org.team_ids, "department": org.department_ids, "region": org.region_ids}[layer][unit]


def _scope_masks(world: World, ing: Ingested, layers=SCOPE_LAYERS) -> list[tuple[str, int, np.ndarray]]:
    out = []
    for layer in layers:
        if layer == "executive":
            out.append(("executive", 0, np.ones(len(ing), dtype=bool)))
            continue
        arr = {"region": ing.region, "department": ing.dept, "team": ing.team}[layer]
        for u in np.unique(arr):
            out.append((layer, int(u), arr == u))
    return out


def _sketch(ing: Ingested, mask: np.ndarray, max_order: int = 3, present: np.ndarray | None = None, weights=None) -> Sketch:
    return Sketch.from_interactions("C", "executive", 0, ing.attrs[mask], ing.labels[mask], ing.worker[mask], ing.team[mask],
                                    ing.dept[mask], ing.region[mask], max_order=max_order,
                                    present=None if present is None else present[mask],
                                    weights=None if weights is None else weights[mask])


def _dedup_weights(fp: np.ndarray) -> np.ndarray:
    _, first = np.unique(fp, return_index=True)
    w = np.zeros(len(fp), dtype=np.int64); w[first] = 1
    return w


def _inject_sketch(injected: list[dict[str, Any]], layer: str = "executive") -> Sketch:
    """Bare claims ingested as facet evidence with their claimed counts (no lineage to verify)."""
    if not injected:
        return Sketch("C", layer, 0)
    ids = np.array([int(d["cell"]) for d in injected], dtype=np.int64)
    counts = np.zeros((len(injected), N_COLS), dtype=np.int64)
    for i, d in enumerate(injected):
        counts[i, COL_N] = int(d["n"]); counts[i, COL_K0 + int(d["label"])] = int(d["k"])
        counts[i, COL_DW] = 1; counts[i, COL_DT] = 1; counts[i, COL_DD] = 1; counts[i, COL_DR] = 1
    return Sketch.pool([Sketch("C", layer, 0, ids, counts)], "C", layer, 0)


# --------------------------------------------------------------------------
# The keyword analyst: beam search over exact facet counts under a query budget
# --------------------------------------------------------------------------
class BeamAnalyst:
    """Explores conjunctions with AND-queries whose facet counts are exact.

    Each queried cell costs one query at one scope.  Level 1 queries all
    order-1 cells organisation-wide, then per region / department / team while
    budget remains; the most promising queried cells (largest label-rate z
    against their parent) are expanded by one attribute.  Only queried cells are
    hypothesis-tested (`search(..., restrict_ids=...)`)."""

    def __init__(self, policy: Policy, budget: int, seed: int, beam: int = 40) -> None:
        self.policy = policy
        self.budget = int(budget)
        self.beam = beam
        self.rng = np.random.default_rng(seed)
        self.queried: dict[tuple[str, int], set[int]] = {}
        self.queries_used = 0

    def _z_scores(self, sk: Sketch, ids: np.ndarray) -> np.ndarray:
        ci = cell_index()
        cnt = sk.lookup(ids)
        n = cnt[:, COL_N]
        first = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
        tot = sk.lookup(first).sum(axis=0)
        p0 = tot[COL_K0:COL_K0 + N_LABELS] / max(int(tot[COL_N]), 1)
        rate = cnt[:, COL_K0:COL_K0 + N_LABELS] / np.maximum(n, 1)[:, None]
        z = (rate - p0[None, :]) / np.sqrt(np.maximum(p0 * (1 - p0), 1e-4) / np.maximum(n, 1)[:, None])
        z[n < self.policy.n_min] = -np.inf
        return z.max(axis=1)

    def explore(self, sketches: dict[tuple[str, int], Sketch], round_budget: int) -> dict[tuple[str, int], np.ndarray]:
        """Return queried ids per scope for this analysis round."""
        ci = cell_index()
        remaining = round_budget
        order1 = np.arange(ci.order_base[1], ci.order_base[1] + ci.order_size[1])
        scopes = sorted(sketches, key=lambda s: SCOPE_LAYERS.index(s[0]))
        out: dict[tuple[str, int], set[int]] = {s: set(self.queried.get(s, ())) for s in scopes}
        # level 1: order-1 facets, top-down until the budget runs out
        for s in scopes:
            if remaining <= 0:
                break
            new = [int(c) for c in order1 if int(c) not in out[s]]
            take = new[:remaining]
            out[s].update(take); remaining -= len(take)
        # expansions: promising cells get one more attribute (all values)
        for s in scopes:
            if remaining <= 0:
                break
            sk = sketches[s]
            ids = np.array(sorted(out[s]), dtype=np.int64)
            if len(ids) == 0 or len(sk.ids) == 0:
                continue
            z = self._z_scores(sk, ids)
            top = ids[np.argsort(-z)[: self.beam]]
            for cell in top:
                if remaining <= 0:
                    break
                sup = ci.super_ids(int(cell))
                if len(sup) == 0:
                    continue
                # only expand into cells the sketch actually contains (facets with n>0)
                present = sup[sk.lookup(sup)[:, COL_N] > 0]
                new = [int(c) for c in present if int(c) not in out[s]]
                take = new[:remaining]
                out[s].update(take); remaining -= len(take)
        self.queries_used += round_budget - remaining
        self.queried = out
        return {s: np.array(sorted(v), dtype=np.int64) for s, v in out.items()}


# --------------------------------------------------------------------------
# RAG retrieval over hashed bag-of-words
# --------------------------------------------------------------------------
class HashedIndex:
    def __init__(self, n_features: int = 2 ** 18) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer
        self.vec = HashingVectorizer(n_features=n_features, alternate_sign=False, norm="l2", token_pattern=r"[A-Za-z0-9_=.\-]+")
        self.matrix = None
        self.doc_idx = np.zeros(0, dtype=np.int64)

    def add(self, texts: list[str], idx: np.ndarray) -> None:
        import scipy.sparse as sp
        m = self.vec.transform(texts)
        self.matrix = m if self.matrix is None else sp.vstack([self.matrix, m], format="csr")
        self.doc_idx = np.concatenate([self.doc_idx, idx])

    def query(self, texts: list[str], top_k: int) -> np.ndarray:
        """Positions (into doc order) of the top_k most similar documents per query; -1 padded."""
        if self.matrix is None:
            return np.full((len(texts), top_k), -1, dtype=np.int64)
        q = self.vec.transform(texts)
        out = np.full((len(texts), top_k), -1, dtype=np.int64)
        n_docs = self.matrix.shape[0]
        k = min(top_k, n_docs)
        for a in range(0, len(texts), 128):
            sims = (q[a:a + 128] @ self.matrix.T).toarray()
            part = np.argpartition(-sims, k - 1, axis=1)[:, :k]
            out[a:a + 128, :k] = part
        return out


def _cell_query_text(cell: int, label: int | None = None) -> str:
    ci = cell_index()
    txt = " ".join(f"{ATTRIBUTES[a]}={VALUES[ATTRIBUTES[a]][v]}" for a, v in ci.decode(int(cell)))
    return txt if label is None else txt + f" errors={LABELS[label]}"


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def run_baseline(world: World, cfg: dict[str, Any], seed: int, name: str, sys_cfg: dict[str, Any],
                 attack_hook=None, failure_plan=None, snapshot_every: int = 1, policy_overrides: dict[str, Any] | None = None,
                 **_: Any) -> dict[str, Any]:
    policy = Policy.from_cfg(cfg, **(policy_overrides or {}))
    edge = get_profile(cfg, cfg["models"]["edge_profile"])
    frontier = get_profile(cfg, cfg["models"].get("frontier_profile", cfg["models"]["edge_profile"]))
    perfect = get_profile(cfg, "sim-perfect") if "sim-perfect" in cfg["models"]["profiles"] else frontier
    records = world.records()
    n_rounds = world.n_rounds
    cadence = int(sys_cfg.get("analysis_every", 3))
    analysis_rounds = sorted(set(list(range(0, n_rounds, cadence)) + [n_rounds - 1]))
    order = np.argsort(records.round, kind="stable")
    bounds = np.searchsorted(records.round[order], np.arange(n_rounds + 1))
    t0 = time.time()
    if failure_plan is not None and hasattr(failure_plan, "attach"):
        try:
            failure_plan.attach(_FakeHier(world))
        except Exception:
            pass

    if name == "B0_isolated":
        result = _run_b0(world, cfg, seed, policy, edge, records, order, bounds, analysis_rounds, attack_hook, failure_plan, snapshot_every)
    else:
        result = _run_central(world, cfg, seed, name, sys_cfg, policy, edge, frontier, perfect, records, order, bounds,
                              analysis_rounds, attack_hook, failure_plan, snapshot_every)
    result["metrics"]["runtime_s"] = time.time() - t0
    result["metrics"]["system"] = name
    return result


class _FakeHier:
    """Minimal object so a FailurePlan can prepare worker-level failures without a hierarchy."""

    def __init__(self, world: World) -> None:
        self.org = world.org
        self.nodes: dict[str, Any] = {}
        self.delay: dict[str, int] = {}
        self.layer_names = ["executive"]
        self.by_layer: dict[str, list] = {}

    def root_node(self):
        return None


def _finish(world: World, cfg: dict[str, Any], policy: Policy, book: ClaimBook, snapshots: dict[int, list[ClaimRecord]],
            *, bytes_off_device: int, canaries_exposed: list[str], tokens_edge: int, tokens_cloud: int, model_calls: int,
            edge, frontier, probe, extra: dict[str, Any], layer_name: str, seed: int) -> dict[str, Any]:
    kb = book.records(world)
    m = match_effects(world, kb, policy.effect_min)
    metrics: dict[str, Any] = {k: v for k, v in m.items() if k not in ("classified", "discovered")}
    metrics.update(evidence_metrics(world, m["discovered"], None))
    fid = transition_fidelity(world, {"executive": book.records(world, include_superseded=False)}, policy.effect_min)
    metrics["fidelity"] = fid["table"]
    metrics["per_layer"] = fid["per_layer"]
    metrics.update(temporal_metrics(world, snapshots))
    metrics.update(contradiction_metrics(world, [], kb))
    raw_bytes = raw_record_bytes(world)
    metrics.update(privacy_metrics(world, canaries_exposed, bytes_off_device, raw_bytes, 0, 0))
    metrics["bytes_transmitted"] = int(bytes_off_device)
    metrics["bytes_worker_to_team"] = int(bytes_off_device)
    metrics["bytes_above_team"] = 0
    metrics["compression_ratio"] = raw_bytes / max(bytes_off_device, 1) if bytes_off_device else None
    metrics["compression_ratio_total"] = metrics["compression_ratio"]
    metrics["tokens"] = int(tokens_edge + tokens_cloud)
    metrics["tokens_to_cloud"] = int(tokens_cloud)
    metrics["model_calls"] = int(model_calls)
    metrics["latency_ms_est"] = float(tokens_edge / 1000 * edge.latency_ms_per_1k_tokens + tokens_cloud / 1000 * frontier.latency_ms_per_1k_tokens)
    metrics["energy_j_est"] = float(tokens_edge / 1000 * edge.energy_j_per_1k_tokens + tokens_cloud / 1000 * frontier.energy_j_per_1k_tokens)
    egress = float(cfg.get("models", {}).get("cloud_egress_usd_per_gb", 0.09))
    metrics["cost_usd_est"] = float(tokens_edge / 1000 * edge.usd_per_1k_tokens + tokens_cloud / 1000 * frontier.usd_per_1k_tokens
                                    + bytes_off_device / 1e9 * egress)
    cl = m["classified"]
    root_poison = sum(1 for c in cl if c["category"] == "poison" and c["status"] == "accepted")
    root_total = sum(1 for c in cl if c["status"] == "accepted")
    metrics["poison"] = {"attack_records": int((world.attack_tag > 0).sum()), "attack_records_kept_at_team": int((world.attack_tag > 0).sum()),
                         "benign_records_dropped": 0, "claims_quarantined": 0, "attack_claims_quarantined": 0,
                         "poison_claims_at_root": root_poison, "poison_promotion_rate": root_poison / max(root_total, 1),
                         "poison_by_layer": {layer_name: root_poison}}
    metrics["n_root_claims"] = root_total
    metrics["stats"] = extra
    metrics["failure_reasons"] = failure_reasons_from_probe(world, m, probe)
    return {"metrics": metrics, "classified": cl, "discovered": m["discovered"], "snapshots": snapshots, "hier": None}


# --------------------------------------------------------------------------
# B0: isolated workers
# --------------------------------------------------------------------------
def _run_b0(world, cfg, seed, policy, edge, records, order, bounds, analysis_rounds, attack_hook, failure_plan, snapshot_every):
    slm = SimulatedSLM(edge, seed * 7919 + 17)
    book = ClaimBook(policy, "worker")
    snapshots: dict[int, list[ClaimRecord]] = {}
    parts: list[Ingested] = []
    tokens = 0; calls = 0
    n_min = policy.n_min
    for r in range(world.n_rounds):
        idx = order[bounds[r]:bounds[r + 1]]
        ing = ingest_round(world, records, idx, slm, attack_hook, failure_plan, r)
        tokens += len(ing) * TOKENS_PER_RECORD; calls += len(ing)
        parts.append(ing)
        if r in analysis_rounds:
            allv = _concat(parts)
            # workers with enough records (a worker needs >= n_min matching records for any claim)
            wk, counts = np.unique(allv.worker, return_counts=True)
            for w in wk[counts >= n_min]:
                mask = allv.worker == w
                sk = _sketch(allv, mask, max_order=3)
                c = search(sk, n_min=n_min, effect_min=policy.effect_min, fdr_q=policy.fdr_q, max_order=3)
                if len(c):
                    book.upsert("worker", int(w), c, r)
                    calls += 1
            # injected claims from compromised devices become their own worker's claims (self-poisoning only)
            for d in allv.injected:
                key = ("worker", int(d["worker"]), int(d["cell"]), int(d["label"]), 1)
                if key not in book.claims:
                    book.claims[key] = {"first_round": r, "status": "accepted", "valid_to": None, "n": int(d["n"]), "k": int(d["k"]),
                                        "rate": d["k"] / max(d["n"], 1), "baseline": 0.0, "effect": d["k"] / max(d["n"], 1), "p": 1e-6,
                                        "q": 1e-4, "conf": float(d.get("confidence", 0.9)), "support": 1.0, "replica": int(d["n"]),
                                        "last_round": r, "dw": 1, "dt": 1, "dd": 1, "dr": 1}
        if r % snapshot_every == 0 or r == world.n_rounds - 1:
            snapshots[r] = book.records(world)
    allv = _concat(parts)

    def probe(cell, label, sl, su):
        m = np.ones(len(allv), dtype=bool)
        for a, v in cell_index().decode(int(cell)):
            m &= allv.attrs[:, a] == v
        best = 0
        if m.any():
            _, cnt = np.unique(allv.worker[m], return_counts=True)
            best = int(cnt.max())
        return {"order_supported": True, "tested_scope": True, "tested_wide": False, "n_scope": best,
                "k_scope": int(mask_matrix(allv.labels[m])[:, label].sum()) if m.any() else 0,
                "untested_reason": "hierarchy_isolation"}

    return _finish(world, cfg, policy, book, snapshots, bytes_off_device=0, canaries_exposed=[], tokens_edge=tokens, tokens_cloud=0,
                   model_calls=calls, edge=edge, frontier=edge, probe=probe, extra={"records_ingested": len(allv)},
                   layer_name="worker", seed=seed)


# --------------------------------------------------------------------------
# Centralized systems
# --------------------------------------------------------------------------
def _run_central(world, cfg, seed, name, sys_cfg, policy, edge, frontier, perfect, records, order, bounds, analysis_rounds,
                 attack_hook, failure_plan, snapshot_every):
    device = SimulatedSLM(perfect, seed * 7919 + 17)        # devices upload exact records
    reader = SimulatedSLM(frontier, seed * 104729 + 3)      # the centre's frontier model (B3 perception)
    book = ClaimBook(policy, "executive")
    snapshots: dict[int, list[ClaimRecord]] = {}
    parts: list[Ingested] = []
    canaries: list[str] = []
    bytes_off = 0; tokens_cloud = 0; calls = 0
    query_budget = int(sys_cfg.get("query_budget", 3000))
    if "query_budget_per_10k" in sys_cfg:   # the analyst's budget grows with the data it is asked to cover
        query_budget = int(float(sys_cfg["query_budget_per_10k"]) * max(1.0, world.n / 10000.0))
    top_k = int(sys_cfg.get("top_k", 50))
    window = int(sys_cfg.get("window_records", 200))
    min_votes = int(sys_cfg.get("min_votes", 3))
    analyst = BeamAnalyst(policy, query_budget, seed)
    index = HashedIndex() if name == "B2_central_rag" else None
    chunk_sketches: list[Sketch] = []
    pending_chunk: list[Ingested] = []
    probe_state: dict[str, Any] = {"queried": {}, "sketches": {}, "retrieved": {}, "votes": {}}
    per_record_bytes = raw_record_bytes(world) / max(world.n, 1)
    W = policy.recent_window

    def frontier_chunks(new: Ingested) -> None:
        nonlocal tokens_cloud, calls
        pending_chunk.append(new)
        buf = _concat(pending_chunk)
        while len(buf) >= window:
            chunk = Ingested(*(getattr(buf, f)[:window] for f in ("idx", "attrs", "labels", "worker", "team", "dept", "region",
                                                                   "fingerprint", "round", "confidence")), [], 0)
            view, present, lab = reader.perceive(chunk.attrs, chunk.labels)
            chunk.attrs = view; chunk.labels = lab
            chunk_sketches.append(_sketch(chunk, np.ones(len(chunk), dtype=bool), 3, present))
            tokens_cloud += window * TOKENS_PER_RECORD; calls += 1
            buf = Ingested(*(getattr(buf, f)[window:] for f in ("idx", "attrs", "labels", "worker", "team", "dept", "region",
                                                                 "fingerprint", "round", "confidence")), [], 0)
        pending_chunk[:] = [buf]

    for r in range(world.n_rounds):
        idx = order[bounds[r]:bounds[r + 1]]
        ing = ingest_round(world, records, idx, device, attack_hook, failure_plan, r)
        parts.append(ing)
        bytes_off += int(len(ing) * per_record_bytes)
        canaries.extend(records.canaries_in(ing.idx))
        if index is not None and len(ing):
            index.add([records.rationale(int(i)) for i in ing.idx], ing.idx)
        if name == "B3_central_llm_summary" and len(ing):
            frontier_chunks(ing)
        if r not in analysis_rounds:
            if r % snapshot_every == 0:
                snapshots[r] = book.records(world)
            continue
        allv = _concat(parts)
        recent_mask = allv.round >= r - W + 1
        if name == "B3_central_llm_summary":
            pooled = Sketch.pool(chunk_sketches + [_inject_sketch(allv.injected)], "C", "executive", r, 3)
            c = search(pooled, n_min=policy.n_min, effect_min=policy.effect_min, fdr_q=policy.fdr_q, max_order=3)
            book.upsert("executive", 0, c, r)
            probe_state["sketches"][("executive", 0)] = pooled
            book.revise(_sketch(allv, recent_mask, 3), "executive", 0, r)
            tokens_cloud += int(pooled.wire_bytes() // 4); calls += 1
        elif name == "ORACLE_central_stats":
            weights = _dedup_weights(allv.fingerprint)
            for sl, su, mask in _scope_masks(world, allv):
                sk = _sketch(allv, mask, 3, weights=weights)
                # order-4 cells with enough support (sparse)
                rows = allv.attrs[mask & (weights > 0)]
                if len(rows) >= policy.n_min:
                    ci = cell_index()
                    ids4 = ci.ids_for_rows(rows, 4).ravel()
                    u, cnt = np.unique(ids4, return_counts=True)
                    keep = u[cnt >= policy.n_min]
                    if len(keep):
                        lab = mask_matrix(allv.labels[mask & (weights > 0)]).astype(np.int64)
                        ids4m = ci.ids_for_rows(rows, 4)
                        sel = np.isin(ids4m, keep)
                        rr, cc = np.nonzero(sel)
                        flat = ids4m[rr, cc]
                        uu, inv = np.unique(flat, return_inverse=True)
                        counts = np.zeros((len(uu), N_COLS), dtype=np.int64)
                        counts[:, COL_N] = np.bincount(inv, minlength=len(uu))
                        for l in range(N_LABELS):
                            counts[:, COL_K0 + l] = np.bincount(inv, weights=lab[rr, l], minlength=len(uu)).astype(np.int64)
                        wk = allv.worker[mask & (weights > 0)][rr]
                        for col, src in ((COL_DW, wk), (COL_DT, world.org.worker_team[wk]), (COL_DD, world.org.worker_department[wk]),
                                         (COL_DR, world.org.worker_region[wk])):
                            key = inv.astype(np.int64) * (int(src.max()) + 1) + src.astype(np.int64)
                            uk = np.unique(key)
                            counts[:, col] = np.bincount(uk // (int(src.max()) + 1), minlength=len(uu))
                        sk = Sketch.pool([sk, Sketch("C", sl, r, uu, counts, 4)], "C", sl, r, 4)
                c = search(sk, n_min=policy.n_min, effect_min=policy.effect_min, fdr_q=policy.fdr_q, max_order=4)
                book.upsert(sl, su, c, r, support_fn=lambda cd, i: _is_from(cd, i, policy))
                book.revise(_sketch(allv, mask & recent_mask, 3), sl, su, r)
                probe_state["sketches"][(sl, su)] = sk
            calls += 1
        else:  # B1 keyword, B2 RAG, B4 majority vote share the analyst
            inj = _inject_sketch(allv.injected)
            sketches = {(sl, su): Sketch.pool([_sketch(allv, mask, 3), inj] if sl == "executive" else [_sketch(allv, mask, 3)], "C", sl, r, 3)
                        for sl, su, mask in _scope_masks(world, allv)}
            queried = analyst.explore(sketches, query_budget)
            probe_state["queried"] = queried; probe_state["sketches"] = sketches
            tokens_cloud += query_budget * 40; calls += 1   # the analyst is a frontier model issuing queries
            if name == "B1_central_keyword":
                for (sl, su), ids in queried.items():
                    if len(ids) == 0:
                        continue
                    c = search(sketches[(sl, su)], n_min=policy.n_min, effect_min=policy.effect_min, fdr_q=policy.fdr_q,
                               max_order=3, restrict_ids=ids)
                    book.upsert(sl, su, c, r)
                    mask = next(m for l, u, m in _scope_masks(world, allv) if l == sl and u == su)
                    book.revise(_sketch(allv, mask & recent_mask, 3), sl, su, r)
            elif name == "B2_central_rag":
                tokens_cloud += _rag_round(world, allv, sketches, queried, index, top_k, policy, book, r, probe_state)
            elif name == "B4_majority_vote":
                _vote_round(world, allv, sketches, queried, policy, book, r, min_votes, probe_state)
        if r % snapshot_every == 0 or r == world.n_rounds - 1:
            snapshots[r] = book.records(world)

    allv = _concat(parts)
    exposed = [c for c in canaries if c]

    def probe(cell, label, sl, su):
        sk = probe_state["sketches"].get((sl, su))
        wide = probe_state["sketches"].get(("executive", 0))
        q = probe_state["queried"]
        tested_scope = (sl, su) in q and int(cell) in set(q[(sl, su)].tolist()) if q else sk is not None
        tested_wide = ("executive", 0) in q and int(cell) in set(q[("executive", 0)].tolist()) if q else wide is not None
        order = int(cell_index().order_of(np.array([cell]))[0])
        f = {"order_supported": order <= (4 if name == "ORACLE_central_stats" else 3), "tested_scope": bool(tested_scope),
             "tested_wide": bool(tested_wide), "untested_reason": "retrieval_failure"}
        if sk is not None:
            cnt = sk.lookup(np.array([cell]))[0]
            f["n_scope"], f["k_scope"] = int(cnt[COL_N]), int(cnt[COL_K0 + label])
        if wide is not None:
            cnt = wide.lookup(np.array([cell]))[0]
            f["n_wide"], f["k_wide"] = int(cnt[COL_N]), int(cnt[COL_K0 + label])
        if name == "B2_central_rag":
            f["retrieved_n"] = probe_state["retrieved"].get((cell, label), 0)
        if name == "B4_majority_vote":
            f["votes_for"] = probe_state["votes"].get((sl, su, cell, label), 0)
        return f

    extra = {"records_ingested": len(allv), "queries_used": analyst.queries_used, "chunks": len(chunk_sketches),
             "injected_claims_ingested": len(allv.injected)}
    return _finish(world, cfg, policy, book, snapshots, bytes_off_device=bytes_off, canaries_exposed=exposed, tokens_edge=0,
                   tokens_cloud=tokens_cloud, model_calls=calls, edge=edge, frontier=frontier, probe=probe, extra=extra,
                   layer_name="executive", seed=seed)


def _is_from(cd: Candidates, i: int, policy: Policy) -> float:
    dr, dd, dt, dw = int(cd.dr[i]), int(cd.dd[i]), int(cd.dt[i]), int(cd.dw[i])
    return float(dr + policy.rho_region * (dd - dr) + policy.rho_department * (dt - dd) + policy.rho_team * (dw - dt))


def _rag_round(world, allv: Ingested, sketches, queried, index: HashedIndex, top_k: int, policy: Policy, book: ClaimBook, r: int,
               probe_state: dict[str, Any]) -> int:
    """Hypotheses (cells) come from the analyst; evidence is the top_k retrieved records only.

    The retrieval query names the cell's attributes but NOT the outcome label, so
    the retrieved set is an (approximately) unbiased sample of matching records;
    every label's rate is then estimated from that sample.  Returns tokens read."""
    ci = cell_index()
    ex = sketches[("executive", 0)]
    first = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
    tot = ex.lookup(first).sum(axis=0)
    p0 = tot[COL_K0:COL_K0 + N_LABELS] / max(int(tot[COL_N]), 1)
    tokens = 0
    for (sl, su), ids in queried.items():
        if len(ids) == 0:
            continue
        cnt = sketches[(sl, su)].lookup(ids)
        # shortlist: testable cells ranked by the analyst's facet z-score (most promising first)
        testable = ids[cnt[:, COL_N] >= policy.n_min]
        if len(testable) == 0:
            continue
        z = BeamAnalyst(policy, 0, 0)._z_scores(sketches[(sl, su)], testable)
        cells = testable[np.argsort(-z)][:400]
        texts = [_cell_query_text(int(c), None) for c in cells]
        hits = index.query(texts, top_k)
        tokens += len(cells) * top_k * TOKENS_PER_RECORD
        n_c = np.zeros(len(cells), dtype=np.int64); k_c = np.zeros((len(cells), N_LABELS), dtype=np.int64)
        # the same interaction test as every other system: outside evidence is the most elevated
        # order-(k-1) sub-cell, which the RAG analyst must also retrieve (k more retrievals per cell)
        n_o = np.zeros((len(cells), N_LABELS), dtype=np.int64); k_o = np.zeros((len(cells), N_LABELS), dtype=np.int64)
        sub_texts, sub_owner = [], []
        for j, c in enumerate(cells):
            pairs = ci.decode(int(c))
            for drop in range(len(pairs)):
                sub = [pv for q_, pv in enumerate(pairs) if q_ != drop]
                sub_texts.append(" ".join(f"{ATTRIBUTES[a]}={VALUES[ATTRIBUTES[a]][v]}" for a, v in sub) if sub else "")
                sub_owner.append((j, drop))
        sub_hits = index.query(sub_texts, top_k) if sub_texts else np.zeros((0, top_k), dtype=np.int64)
        tokens += len(sub_texts) * top_k * TOKENS_PER_RECORD
        unit_arr = None if sl == "executive" else {"region": world.region, "department": world.department, "team": world.team}[sl]
        for j, c in enumerate(cells):
            pos = hits[j][hits[j] >= 0]
            if len(pos) == 0:
                continue
            rec = index.doc_idx[pos]
            m = np.ones(len(pos), dtype=bool)
            attrs = world.attrs[rec]
            for at, v in ci.decode(int(c)):
                m &= attrs[:, at] == v
            if unit_arr is not None:
                m &= unit_arr[rec] == su
            n_c[j] = int(m.sum())
            if m.any():
                k_c[j] = mask_matrix(world.observed_labels[rec[m]]).sum(axis=0)
            for l in range(N_LABELS):
                probe_state["retrieved"][(int(c), l)] = max(probe_state["retrieved"].get((int(c), l), 0), int(n_c[j]))
        # outside rates from the sub-cell retrievals (records matching the sub-cell but not the full cell)
        best_rate = np.full((len(cells), N_LABELS), -1.0)
        for s_i, (j, drop) in enumerate(sub_owner):
            pos = sub_hits[s_i][sub_hits[s_i] >= 0]
            if len(pos) == 0:
                continue
            rec = index.doc_idx[pos]
            attrs = world.attrs[rec]
            pairs = ci.decode(int(cells[j]))
            m_sub = np.ones(len(pos), dtype=bool); m_full = np.ones(len(pos), dtype=bool)
            for q_, (at, v) in enumerate(pairs):
                hit = attrs[:, at] == v
                m_full &= hit
                if q_ != drop:
                    m_sub &= hit
            if unit_arr is not None:
                m_sub &= unit_arr[rec] == su
            m_out = m_sub & ~m_full
            if int(m_out.sum()) < max(3, policy.n_min // 2):
                continue
            ko = mask_matrix(world.observed_labels[rec[m_out]]).sum(axis=0)
            rate = ko / int(m_out.sum())
            better = rate > best_rate[j]
            best_rate[j, better] = rate[better]; n_o[j, better] = int(m_out.sum()); k_o[j, better] = ko[better]
        # fall back to the global marginal where no sub-cell sample was retrievable
        nofb = n_o == 0
        n_o[nofb] = max(int(tot[COL_N]), 1)
        k_o[nofb] = (np.broadcast_to(p0[None, :], n_o.shape)[nofb] * max(int(tot[COL_N]), 1)).astype(np.int64)
        ok = n_c >= policy.n_min
        if not ok.any():
            continue
        cc, ll = np.nonzero(ok[:, None] & np.ones((len(cells), N_LABELS), dtype=bool))
        n_v = n_c[cc]; k_v = k_c[cc, ll]
        n_o = n_o[cc, ll]; k_o = k_o[cc, ll]
        base_v = k_o / np.maximum(n_o, 1)
        sign = np.ones(len(cc), dtype=int)
        p = rate_test(n_v, k_v, n_o, k_o, sign)
        eff = k_v / np.maximum(n_v, 1) - base_v
        q = bh_qvalues(p)   # family = tested cells x labels, exactly as hypothesis.search
        keep = np.flatnonzero((q < policy.fdr_q) & (eff >= policy.effect_min))
        if len(keep) == 0:
            continue
        cands = Candidates(cell=cells[cc[keep]], label=ll[keep].astype(np.int64), sign=np.ones(len(keep), dtype=np.int64),
                           n=n_v[keep], k=k_v[keep], rate=k_v[keep] / np.maximum(n_v[keep], 1), baseline=base_v[keep],
                           effect=eff[keep], p=p[keep], q=q[keep], n_out=n_o[keep],
                           dw=n_v[keep], dt=np.ones(len(keep), dtype=np.int64), dd=np.ones(len(keep), dtype=np.int64),
                           dr=np.ones(len(keep), dtype=np.int64), n_tested=len(p))
        book.upsert(sl, su, cands, r)
    return tokens


def _vote_round(world, allv: Ingested, sketches, queried, policy: Policy, book: ClaimBook, r: int, min_votes: int,
                probe_state: dict[str, Any]) -> None:
    """Each worker with >= 4 matching records votes; a 60% supermajority of voters (and >= min_votes) accepts."""
    ci = cell_index()
    lab_all = mask_matrix(allv.labels).astype(np.int64)
    # per-worker overall label rates (the voter's own baseline)
    wk, inv = np.unique(allv.worker, return_inverse=True)
    n_w = np.bincount(inv, minlength=len(wk))
    k_w = np.stack([np.bincount(inv, weights=lab_all[:, l], minlength=len(wk)) for l in range(N_LABELS)], axis=1)
    base_w = k_w / np.maximum(n_w, 1)[:, None]
    for (sl, su), ids in queried.items():
        if len(ids) == 0:
            continue
        cnt = sketches[(sl, su)].lookup(ids)
        first = np.arange(ci.order_base[1], ci.order_base[1] + int(ci.combo_size[1][0]))
        tot = sketches[(sl, su)].lookup(first).sum(axis=0)
        p0 = tot[COL_K0:COL_K0 + N_LABELS] / max(int(tot[COL_N]), 1)
        rate = cnt[:, COL_K0:COL_K0 + N_LABELS] / np.maximum(cnt[:, COL_N], 1)[:, None]
        cand = np.argwhere((rate >= p0[None, :] + policy.effect_min / 2) & (cnt[:, COL_N][:, None] >= policy.n_min))[:400]
        if len(cand) == 0:
            continue
        scope_mask = np.ones(len(allv), dtype=bool) if sl == "executive" else \
            {"region": allv.region, "department": allv.dept, "team": allv.team}[sl] == su
        rows = []
        for a, l in cand:
            pairs = ci.decode(int(ids[a]))
            m = scope_mask.copy()
            for at, v in pairs:
                m &= allv.attrs[:, at] == v
            if not m.any():
                continue
            wi = inv[m]
            n_wm = np.bincount(wi, minlength=len(wk)); k_wm = np.bincount(wi, weights=lab_all[m, l], minlength=len(wk))
            voters = n_wm >= 4
            if voters.sum() == 0:
                continue
            # each voter's baseline: its own rate on the most elevated sub-cell outside the cell (falls back to its overall rate)
            base_v = base_w[:, l].copy()
            if len(pairs) > 1:
                for drop in range(len(pairs)):
                    ms = scope_mask.copy()
                    for q_, (at, v) in enumerate(pairs):
                        if q_ != drop:
                            ms &= allv.attrs[:, at] == v
                    ms &= ~m
                    if not ms.any():
                        continue
                    ws = inv[ms]
                    n_s = np.bincount(ws, minlength=len(wk)); k_s = np.bincount(ws, weights=lab_all[ms, l], minlength=len(wk))
                    r_s = np.where(n_s >= 3, k_s / np.maximum(n_s, 1), -1.0)
                    base_v = np.maximum(base_v, r_s)
            for_ = int(((k_wm / np.maximum(n_wm, 1)) >= base_v + policy.effect_min)[voters].sum())
            against = int(voters.sum()) - for_
            probe_state["votes"][(sl, su, int(ids[a]), int(l))] = for_
            if for_ >= min_votes and for_ >= 0.6 * (for_ + against):
                n_c, k_c = int(m.sum()), int(lab_all[m, l].sum())
                rows.append((int(ids[a]), int(l), n_c, k_c, float(p0[l]), for_, against))
        if not rows:
            continue
        cells = np.array([x[0] for x in rows]); labels = np.array([x[1] for x in rows])
        n_c = np.array([x[2] for x in rows]); k_c = np.array([x[3] for x in rows]); base = np.array([x[4] for x in rows])
        votes = np.array([x[5] for x in rows])
        # vote systems carry no p-value; report the vote share as pseudo-confidence
        share = votes / np.maximum(votes + np.array([x[6] for x in rows]), 1)
        cands = Candidates(cell=cells, label=labels, sign=np.ones(len(rows), dtype=np.int64), n=n_c, k=k_c,
                           rate=k_c / np.maximum(n_c, 1), baseline=base, effect=k_c / np.maximum(n_c, 1) - base,
                           p=1 - share, q=1 - share, n_out=np.zeros(len(rows), dtype=np.int64), dw=votes, dt=votes,
                           dd=votes, dr=votes, n_tested=len(cand))
        book.upsert(sl, su, cands, r, support_fn=lambda cd, i: float(cd.dw[i]))

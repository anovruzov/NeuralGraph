"""World generation for the large-scale Agentic Web stress test.

Builds a heterogeneous decentralised agent network and a body of knowledge whose
evidence carries explicit lineage, including *correlated* copies: many observations
that descend from a single origin.  Nothing here knows about the systems under
test; the same world is handed to every system so comparisons are paired.

Object model
------------
K_i = (c_i, E_i, L_i, t_i)
  c_i  claim              -> `claim_value_true`  (current ground truth)
  E_i  supporting evidence -> `item_*` arrays (each item lives on a home node)
  L_i  lineage             -> `item_root` -> `root_*` arrays (origin sources)
  t_i  temporal state      -> observation times vs. revision times (staleness)

A *lineage root* is an independent origin observation.  A *family* is the set of
evidence items derived from one root.  Ten items from one root are ten replicas
of one observation, not ten independent sources -- that distinction is the whole
point of the experiment, so it is materialised in the data, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# claim classes -- used to isolate the correlated-replication test of section 7
CLASS_NORMAL = 0
CLASS_CORRELATED = 1   # one root, large derived family (e0 -> e1..e100)
CLASS_INDEPENDENT = 2   # three roots, one item each


@dataclass
class World:
    """Immutable-by-convention container of numpy arrays describing one world."""

    seed: int
    config: dict[str, Any]

    # --- network ---------------------------------------------------------
    n_nodes: int = 0
    node_org: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    node_team: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    node_region: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    node_domain: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    node_specialty: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    domain_org: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    domain_region: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    domain_ptr: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    domain_members: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    org_ptr: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    org_members: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    region_ptr: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    region_members: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))

    # --- claims ----------------------------------------------------------
    n_claims: int = 0
    claim_value_true: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))
    claim_value_prev: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))
    claim_value_false: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))
    claim_revised: np.ndarray = field(default_factory=lambda: np.empty(0, bool))
    claim_revision_time: np.ndarray = field(default_factory=lambda: np.empty(0, np.float32))
    claim_importance: np.ndarray = field(default_factory=lambda: np.empty(0, np.float32))
    claim_specialty: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    claim_class: np.ndarray = field(default_factory=lambda: np.empty(0, np.int8))
    claim_root_ptr: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    claim_item_ptr: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))

    # --- lineage roots ---------------------------------------------------
    n_roots: int = 0
    root_claim: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    root_origin_node: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    root_obs_time: np.ndarray = field(default_factory=lambda: np.empty(0, np.float32))
    root_stale: np.ndarray = field(default_factory=lambda: np.empty(0, bool))

    # --- evidence items (sorted by claim) --------------------------------
    n_items: int = 0
    item_claim: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    item_root: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    item_home: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    item_stale: np.ndarray = field(default_factory=lambda: np.empty(0, bool))

    # --- derived ---------------------------------------------------------
    claim_n_roots: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    claim_n_items: np.ndarray = field(default_factory=lambda: np.empty(0, np.int32))
    # per (claim, value code) jitter used to break aggregation ties.  Ties must
    # NOT resolve toward the lowest code, because code 0 is the true value:
    # that would hand every system free accuracy, and hand more of it to systems
    # whose support counts are small integers (i.e. lineage aggregation).
    code_tiebreak: np.ndarray = field(default_factory=lambda: np.empty((0, 0), np.float32))

    def item_slice(self, claim: int) -> slice:
        return slice(int(self.claim_item_ptr[claim]), int(self.claim_item_ptr[claim + 1]))


def _pareto_ints(rng: np.random.Generator, size: int, alpha: float, cap: int) -> np.ndarray:
    """Heavy-tailed positive integers in [1, cap]."""
    raw = (rng.pareto(alpha, size) + 1.0)
    vals = np.floor(raw).astype(np.int64)
    return np.clip(vals, 1, cap).astype(np.int32)


def build_world(seed: int, config: dict[str, Any]) -> World:
    rng = np.random.default_rng(seed * 1_000_003 + 17)
    n = int(config["n_agents"])

    # ---------------- network topology -----------------------------------
    n_regions = int(config.get("n_regions", 8))
    nodes_per_domain = int(config.get("nodes_per_domain", 25))
    domains_per_org = int(config.get("domains_per_org", 4))

    n_domains = max(n_regions * 2, int(np.ceil(n / nodes_per_domain)))
    n_orgs = max(2, int(np.ceil(n_domains / domains_per_org)))

    domain_org = (np.arange(n_domains, dtype=np.int32) % n_orgs).astype(np.int32)
    # regions cut across organisations: a failure domain is (org, region) specific,
    # so removing a region removes parts of many orgs and vice versa.
    domain_region = rng.integers(0, n_regions, n_domains).astype(np.int32)

    # assign nodes to domains with unequal domain sizes (heavy tail of org size)
    weights = rng.gamma(2.0, 1.0, n_domains)
    weights /= weights.sum()
    node_domain = rng.choice(n_domains, size=n, p=weights).astype(np.int32)
    node_org = domain_org[node_domain]
    node_region = domain_region[node_domain]
    n_specialties = int(config.get("n_specialties", 12))
    node_team = (node_domain.astype(np.int64) * 7 + rng.integers(0, 5, n)).astype(np.int32)
    # specialty correlates with organisation (teams know their own area)
    node_specialty = ((node_org.astype(np.int64) * 3 + rng.integers(0, 3, n)) % n_specialties).astype(np.int32)

    order = np.argsort(node_domain, kind="stable")
    domain_members = order.astype(np.int32)
    counts = np.bincount(node_domain, minlength=n_domains)
    domain_ptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int32)

    order_r = np.argsort(node_region, kind="stable")
    region_members = order_r.astype(np.int32)
    counts_r = np.bincount(node_region, minlength=n_regions)
    region_ptr = np.concatenate([[0], np.cumsum(counts_r)]).astype(np.int64)

    order_o = np.argsort(node_org, kind="stable")
    org_members = order_o.astype(np.int32)
    counts_o = np.bincount(node_org, minlength=n_orgs)
    org_ptr = np.concatenate([[0], np.cumsum(counts_o)]).astype(np.int32)

    # ---------------- claims ---------------------------------------------
    m = int(max(config.get("claim_floor", 2000), round(config.get("claims_per_node", 2.0) * n)))
    claim_value_true = rng.integers(0, 1_000_000, m).astype(np.int64)
    claim_value_prev = (claim_value_true + 1 + rng.integers(0, 999, m)) % 1_000_000
    claim_value_false = (claim_value_true + 500_001 + rng.integers(0, 999, m)) % 1_000_000
    claim_importance = np.float32(1.0) / _pareto_ints(rng, m, 1.5, 100).astype(np.float32)
    claim_importance = (claim_importance / claim_importance.max()).astype(np.float32)
    claim_specialty = rng.integers(0, n_specialties, m).astype(np.int32)

    revision_fraction = float(config.get("revision_fraction", 0.25))
    claim_revised = rng.random(m) < revision_fraction
    claim_revision_time = rng.uniform(0.2, 0.8, m).astype(np.float32)
    claim_value_prev = np.where(claim_revised, claim_value_prev, claim_value_true)

    # claim classes: a designated subset isolates the correlated-replication test
    frac_corr = float(config.get("frac_class_correlated", 0.02))
    frac_indep = float(config.get("frac_class_independent", 0.02))
    u = rng.random(m)
    claim_class = np.where(u < frac_corr, CLASS_CORRELATED,
                           np.where(u < frac_corr + frac_indep, CLASS_INDEPENDENT, CLASS_NORMAL)).astype(np.int8)

    # ---------------- lineage roots --------------------------------------
    # root-count mixture; a deliberate share of claims has a *single* origin,
    # which is a regime where lineage awareness cannot help by construction.
    mix = config.get("root_mix", [0.35, 0.40, 0.25])
    r = rng.random(m)
    n_roots_per_claim = np.where(
        r < mix[0], 1,
        np.where(r < mix[0] + mix[1], rng.integers(2, 4, m), rng.integers(4, 9, m)),
    ).astype(np.int32)
    n_roots_per_claim = np.where(claim_class == CLASS_CORRELATED, 1, n_roots_per_claim)
    n_roots_per_claim = np.where(claim_class == CLASS_INDEPENDENT, 3, n_roots_per_claim)

    claim_root_ptr = np.concatenate([[0], np.cumsum(n_roots_per_claim)]).astype(np.int64)
    n_r = int(claim_root_ptr[-1])
    root_claim = np.repeat(np.arange(m, dtype=np.int32), n_roots_per_claim)

    # family sizes: heavy tailed, so a minority of roots dominate apparent support
    fam_alpha = float(config.get("family_alpha", 1.1))
    fam_cap = int(config.get("family_cap", 40))
    family_size = _pareto_ints(rng, n_r, fam_alpha, fam_cap)
    corr_root = claim_class[root_claim] == CLASS_CORRELATED
    family_size = np.where(corr_root, int(config.get("correlated_family_size", 100)), family_size)
    family_size = np.where(claim_class[root_claim] == CLASS_INDEPENDENT, 1, family_size).astype(np.int32)

    # origin nodes: biased to a domain whose org matches the claim specialty
    origin_domain = rng.integers(0, n_domains, n_r).astype(np.int32)
    dsize = (domain_ptr[1:] - domain_ptr[:-1]).astype(np.int64)
    # guard against empty domains
    empty = dsize[origin_domain] == 0
    while empty.any():
        origin_domain[empty] = rng.integers(0, n_domains, int(empty.sum())).astype(np.int32)
        empty = dsize[origin_domain] == 0
    off = (rng.random(n_r) * dsize[origin_domain]).astype(np.int64)
    root_origin_node = domain_members[domain_ptr[origin_domain] + off].astype(np.int32)

    root_obs_time = rng.random(n_r).astype(np.float32)
    root_stale = (root_obs_time < claim_revision_time[root_claim]) & claim_revised[root_claim]

    # ---------------- evidence items -------------------------------------
    item_root = np.repeat(np.arange(n_r, dtype=np.int32), family_size)
    n_i = item_root.size
    item_claim = root_claim[item_root]
    item_stale = root_stale[item_root]

    # homes: derived copies cluster around the origin (same domain / org),
    # which is why a correlated family dies together under domain failure.
    p_same_domain = float(config.get("home_same_domain", 0.60))
    p_same_org = float(config.get("home_same_org", 0.30))
    u2 = rng.random(n_i)
    home = np.empty(n_i, np.int32)

    od = origin_domain[item_root]
    m_dom = u2 < p_same_domain
    if m_dom.any():
        idx = np.where(m_dom)[0]
        d = od[idx]
        off = (rng.random(idx.size) * dsize[d]).astype(np.int64)
        home[idx] = domain_members[domain_ptr[d] + off]
    m_org = (~m_dom) & (u2 < p_same_domain + p_same_org)
    if m_org.any():
        idx = np.where(m_org)[0]
        o = domain_org[od[idx]]
        osize = (org_ptr[1:] - org_ptr[:-1]).astype(np.int64)
        off = (rng.random(idx.size) * np.maximum(osize[o], 1)).astype(np.int64)
        home[idx] = org_members[org_ptr[o] + off]
    m_glob = ~(m_dom | m_org)
    if m_glob.any():
        home[m_glob] = rng.integers(0, n, int(m_glob.sum())).astype(np.int32)
    # the origin observation itself lives on the origin node
    first_of_root = np.concatenate([[0], np.cumsum(family_size)[:-1]]).astype(np.int64)
    home[first_of_root] = root_origin_node

    # items are already grouped by claim because roots are grouped by claim
    claim_item_counts = np.bincount(item_claim, minlength=m)
    claim_item_ptr = np.concatenate([[0], np.cumsum(claim_item_counts)]).astype(np.int64)

    n_codes = int(config.get("max_value_codes", 8))
    code_tiebreak = rng.random((m, n_codes)).astype(np.float32)

    return World(
        seed=seed,
        config=dict(config),
        n_nodes=n,
        node_org=node_org, node_team=node_team, node_region=node_region,
        node_domain=node_domain, node_specialty=node_specialty,
        domain_org=domain_org, domain_region=domain_region,
        domain_ptr=domain_ptr, domain_members=domain_members,
        org_ptr=org_ptr, org_members=org_members,
        region_ptr=region_ptr, region_members=region_members,
        n_claims=m,
        claim_value_true=claim_value_true, claim_value_prev=claim_value_prev,
        claim_value_false=claim_value_false,
        claim_revised=claim_revised, claim_revision_time=claim_revision_time,
        claim_importance=claim_importance, claim_specialty=claim_specialty,
        claim_class=claim_class,
        claim_root_ptr=claim_root_ptr.astype(np.int64),
        claim_item_ptr=claim_item_ptr.astype(np.int64),
        n_roots=n_r, root_claim=root_claim, root_origin_node=root_origin_node,
        root_obs_time=root_obs_time, root_stale=root_stale,
        n_items=n_i, item_claim=item_claim, item_root=item_root,
        item_home=home, item_stale=item_stale,
        claim_n_roots=n_roots_per_claim,
        claim_n_items=claim_item_counts.astype(np.int32),
        code_tiebreak=code_tiebreak,
    )


def world_summary(w: World) -> dict[str, Any]:
    return {
        "n_agents": int(w.n_nodes),
        "n_orgs": int(w.org_ptr.size - 1),
        "n_failure_domains": int(w.domain_ptr.size - 1),
        "n_regions": int(w.domain_region.max() + 1),
        "n_claims": int(w.n_claims),
        "n_lineage_roots": int(w.n_roots),
        "n_evidence_items": int(w.n_items),
        "mean_roots_per_claim": float(w.claim_n_roots.mean()),
        "mean_items_per_claim": float(w.claim_n_items.mean()),
        "max_items_per_claim": int(w.claim_n_items.max()),
        "frac_single_root_claims": float((w.claim_n_roots == 1).mean()),
        "frac_revised_claims": float(w.claim_revised.mean()),
        "frac_stale_items": float(w.item_stale.mean()),
    }

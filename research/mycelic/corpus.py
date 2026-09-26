"""Synthetic enterprise corpus with hidden, verifiable ground truth.

Design goals (each one is a defence against a specific way of rigging the
benchmark in the hierarchy's favour):

1. The *content* of a record is a structured tuple (predicate, anchor entity,
   aux entities, timestamp, event id, polarity).  Surface text is a lossy,
   synonym-noised realisation of that tuple.  Every system under test reads
   the same surface text through the same (tier-dependent) extraction
   operator, so no architecture gets a privileged view.

2. A *strategic pattern* is a contiguous path in a causal predicate schema,
   bound to ONE anchor entity, with non-decreasing timestamps, whose facets
   are deliberately scattered across distant org branches.  It is therefore
   invisible to any single user/team/department by construction - but it is
   equally visible to a centralised system that manages to get all the facet
   records into one context.  The hierarchy gets no free lunch: its only
   possible advantage is *finding* the records under a token budget.

3. Decoys are generated with the same surface statistics as real patterns and
   differ only in verifiable structure:
      D1 entity-coincidence  - shared anchor, predicates from different chains
      D2 temporal-scramble   - right chain + anchor, wrong time order
      D3 near-miss entity    - right chain + order, lexically-similar but
                               DIFFERENT anchor entities per facet
      D4 duplicate-inflation - one real event echoed by many users in many
                               branches: looks like broad independent support
   Distinguishing them requires four independent checks (entity identity,
   causal-schema membership, temporal order, source independence).  A system
   that does none of them scores ~50% precision by construction.

4. Rare-signal patterns are supported by 1-2 users per facet out of the whole
   enterprise, so they must actually be found rather than handed over.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .org import Org, USER, TEAM, DEPT, SITE, REGION, ENT

# --------------------------------------------------------------------------
# Predicate / causal schema
# --------------------------------------------------------------------------

# Each chain is an ordered list of predicate names.  A real strategic pattern
# is a contiguous sub-path of length >= 3 of exactly one chain.
CAUSAL_CHAINS: List[List[str]] = [
    ["supplier_substitution", "component_defect", "yield_drop",
     "shipment_delay", "customer_escalation", "revenue_miss"],
    ["cert_rotation", "auth_failure", "api_error_spike",
     "checkout_drop", "revenue_miss"],
    ["policy_change", "manual_workaround", "data_exposure_risk",
     "audit_finding", "regulatory_notice"],
    ["attrition_spike", "backlog_growth", "sla_breach",
     "contract_penalty", "account_churn"],
    ["firmware_update", "sensor_drift", "false_alarm",
     "unplanned_downtime", "output_shortfall"],
    ["price_increase", "margin_compression", "discount_escalation",
     "forecast_revision"],
    ["network_change", "latency_regression", "batch_job_overrun",
     "reporting_delay", "close_delay"],
]

# background predicates that carry no strategic meaning
BACKGROUND_PREDICATES = [
    "status_update", "meeting_note", "ticket_triage", "doc_review",
    "onboarding", "budget_note", "training_session", "tool_upgrade",
    "headcount_note", "travel_note", "vendor_checkin", "release_note",
    "backup_completed", "access_request", "inventory_count", "patch_window",
]

PREDICATES: List[str] = []
for ch in CAUSAL_CHAINS:
    for p in ch:
        if p not in PREDICATES:
            PREDICATES.append(p)
N_CAUSAL_PRED = len(PREDICATES)
PREDICATES.extend(BACKGROUND_PREDICATES)
PRED_ID = {p: i for i, p in enumerate(PREDICATES)}
CHAIN_OF_PRED: Dict[int, List[Tuple[int, int]]] = {}
for ci, ch in enumerate(CAUSAL_CHAINS):
    for pos, p in enumerate(ch):
        CHAIN_OF_PRED.setdefault(PRED_ID[p], []).append((ci, pos))

# Surface synonyms: several ways of saying the same predicate.  Extraction has
# to map surface -> predicate, which is where extraction recall/precision bite.
PRED_SURFACE: Dict[str, List[str]] = {
    "supplier_substitution": ["second source part swap", "alt vendor substitution", "supplier switched"],
    "component_defect": ["defective lot found", "component out of spec", "part failure rate up"],
    "yield_drop": ["line yield fell", "first pass yield down", "scrap rate climbing"],
    "shipment_delay": ["shipment slipped", "delivery pushed out", "freight delayed"],
    "customer_escalation": ["customer escalated", "exec escalation opened", "account raised severity"],
    "revenue_miss": ["booked below plan", "revenue shortfall", "missed quarter target"],
    "cert_rotation": ["certificate rotated", "tls cert rollover", "key rotation performed"],
    "auth_failure": ["auth failures spiked", "token validation errors", "login rejections up"],
    "api_error_spike": ["5xx rate elevated", "api errors surged", "error budget burned"],
    "checkout_drop": ["checkout conversion fell", "cart completion down", "payment success rate dipped"],
    "policy_change": ["policy updated", "control tightened", "new approval rule"],
    "manual_workaround": ["team used a workaround", "manual bypass in use", "shadow process adopted"],
    "data_exposure_risk": ["unencrypted export observed", "data left the boundary", "exposure risk flagged"],
    "audit_finding": ["audit finding raised", "control gap logged", "assessor noted issue"],
    "regulatory_notice": ["regulator inquiry received", "compliance notice", "supervisory letter"],
    "attrition_spike": ["several resignations", "attrition above baseline", "team lost engineers"],
    "backlog_growth": ["queue depth rising", "backlog grew", "work in progress piling up"],
    "sla_breach": ["sla missed", "response time out of target", "breached service level"],
    "contract_penalty": ["penalty clause triggered", "service credits owed", "contractual fine"],
    "account_churn": ["customer did not renew", "account churned", "logo lost"],
    "firmware_update": ["firmware pushed", "controller flashed", "device image updated"],
    "sensor_drift": ["sensor readings drifting", "calibration off", "telemetry bias observed"],
    "false_alarm": ["spurious alarm", "nuisance trip", "false positive alert"],
    "unplanned_downtime": ["line stopped unexpectedly", "unplanned outage", "asset went down"],
    "output_shortfall": ["output below plan", "production short", "throughput miss"],
    "price_increase": ["price raised", "list price up", "surcharge applied"],
    "margin_compression": ["gross margin down", "margin squeezed", "cost of goods up"],
    "discount_escalation": ["deeper discounting", "discount approvals rising", "price concessions"],
    "forecast_revision": ["forecast cut", "plan revised down", "guidance changed"],
    "network_change": ["network config change", "routing updated", "firewall rule change"],
    "latency_regression": ["p99 latency up", "response times regressed", "slow queries"],
    "batch_job_overrun": ["nightly job overran", "batch window exceeded", "etl ran long"],
    "reporting_delay": ["report late", "dashboard stale", "metrics delayed"],
    "close_delay": ["month end close slipped", "books closed late", "financial close delayed"],
}
for bp in BACKGROUND_PREDICATES:
    PRED_SURFACE[bp] = [bp.replace("_", " "), bp.replace("_", " ") + " logged",
                        "routine " + bp.replace("_", " ")]

FILLER = ("per the runbook during the window as discussed with the team "
          "ticket reference attached no further action for now follow up next "
          "week owner assigned pending review see thread for details minor "
          "impact expected nothing blocking so far context below").split()

ENTITY_KINDS = ["vendor", "component", "system", "product", "process", "site_asset"]
ENTITY_STEMS = [
    "nimbus", "veridian", "kestrel", "orion", "cobalt", "tessera", "halcyon",
    "meridian", "pellucid", "quarry", "basalt", "lumen", "arbor", "cinder",
    "solace", "vantage", "thistle", "umbra", "wexford", "zephyr", "granite",
    "marlin", "osprey", "peregrine", "sable", "tundra", "vireo", "wisteria",
]
ENTITY_SUFFIX = ["", "-pay", "-core", "-edge", "x", "-ii", "-pro", "-lite",
                 "-eu", "-na", "-apac", "-v2", "-mk3", "-hub", "-net"]

KIND_ROUTINE, KIND_FACET, KIND_DECOY, KIND_DUP, KIND_STALE, KIND_FALSE, \
    KIND_ANOMALY, KIND_NOTABLE = range(8)
KIND_NAMES = ["routine", "facet", "decoy", "dup", "stale", "false", "anomaly",
              "notable"]

REC_DTYPE = np.dtype([
    ("uid", np.int32), ("t", np.int16), ("pred", np.int16),
    ("anchor", np.int32), ("aux", np.int32), ("event", np.int32),
    ("kind", np.int8), ("veracity", np.int8), ("polarity", np.int8),
    ("group", np.int32),   # pattern id (>=0) or decoy id (-2-d) or -1
    ("facet", np.int8), ("superseded", np.int8), ("salience", np.float32),
])


@dataclass
class Pattern:
    pid: int
    chain: int
    start_pos: int
    preds: List[int]
    anchor: int
    facet_records: List[List[int]]     # record ids per facet
    facet_users: List[List[int]]
    facet_times: List[int]
    real: bool
    decoy_type: int = 0                # 0 real, 1..4 decoy kinds
    rare: bool = False
    n_regions: int = 0
    n_sites: int = 0
    contradicted: bool = False
    family: int = -1                   # variant-family id (cross-region variants)


@dataclass
class Corpus:
    org: Org
    recs: np.ndarray
    user_slices: np.ndarray            # (n_users+1,) offsets into recs, sorted by uid
    patterns: List[Pattern]
    entities: List[str]
    entity_kind: np.ndarray
    n_days: int
    seed: int
    cfg: Dict[str, object]
    notable_records: List[int] = field(default_factory=list)
    families: Dict[int, List[int]] = field(default_factory=dict)
    contradiction_pairs: List[Tuple[int, int]] = field(default_factory=list)
    dup_events: Dict[int, List[int]] = field(default_factory=dict)

    # ---------------- surface realisation -------------------------------
    def tokens(self, rid: int) -> List[str]:
        """Surface realisation.

        The synonym and filler choices are keyed on the EVENT id, not the
        record id, so an echo of an event repeats the original wording almost
        verbatim while two independent observations of the same
        (predicate, anchor) differ in filler.  Duplicate detection is
        therefore a text-similarity problem available to every system under
        test - no system is handed the event id.
        """
        r = self.recs[rid]
        h = int.from_bytes(hashlib.blake2b(int(r["event"]).to_bytes(8, "little"),
                                           digest_size=8).digest(), "little")
        pname = PREDICATES[int(r["pred"])]
        surf = PRED_SURFACE[pname][h % len(PRED_SURFACE[pname])]
        toks = surf.split()
        toks.append(self.entities[int(r["anchor"])])
        if r["aux"] >= 0:
            toks.append(self.entities[int(r["aux"])])
        nf = 4 + (h >> 7) % 7
        for i in range(nf):
            toks.append(FILLER[(h >> (3 * i + 11)) % len(FILLER)])
        # paraphrase noise so echoes are near- not exact-duplicates
        hr = int.from_bytes(hashlib.blake2b(int(rid).to_bytes(8, "little"),
                                            digest_size=4).digest(), "little")
        if hr % 100 < 35 and len(toks) > 3:
            toks[-1] = FILLER[(hr >> 7) % len(FILLER)]
        if r["polarity"] < 0:
            toks.append("not")
        return toks

    def text(self, rid: int) -> str:
        r = self.recs[rid]
        node = self.org.nodes[int(r["uid"])]
        return (f"[d{int(r['t']):03d}] {node.name} :: " + " ".join(self.tokens(rid)))

    def recs_of_user(self, uid_index: int) -> np.ndarray:
        a, b = self.user_slices[uid_index], self.user_slices[uid_index + 1]
        return np.arange(a, b)

    def stats(self) -> Dict[str, object]:
        k = np.bincount(self.recs["kind"], minlength=8)
        real = [p for p in self.patterns if p.real]
        dec = [p for p in self.patterns if not p.real]
        return {
            "n_records": int(len(self.recs)),
            "n_users": len(self.org.user_ids),
            "recs_per_user_mean": round(len(self.recs) / len(self.org.user_ids), 1),
            "kinds": {KIND_NAMES[i]: int(k[i]) for i in range(8)},
            "n_entities": len(self.entities),
            "n_real_patterns": len(real),
            "n_rare_patterns": sum(1 for p in real if p.rare),
            "n_decoys": len(dec),
            "decoy_types": {t: sum(1 for p in dec if p.decoy_type == t)
                            for t in (1, 2, 3, 4, 5)},
            "n_families": len(self.families),
            "n_notable": len(self.notable_records),
            "n_contradictions": len(self.contradiction_pairs),
            "pattern_region_spread": {
                "mean": round(float(np.mean([p.n_regions for p in real])), 2),
                "min": int(min(p.n_regions for p in real)),
                "max": int(max(p.n_regions for p in real)),
            } if real else {},
        }


DEFAULT_CFG: Dict[str, object] = {
    "recs_per_user_median": 28.0,
    "recs_per_user_sigma": 0.55,
    "recs_per_user_lo": 6,
    "recs_per_user_hi": 160,
    "n_days": 180,
    "entities_per_1k_users": 60,
    "min_entities": 150,
    "p_dup": 0.09,          # fraction of records that echo another user's event
    "p_stale": 0.06,
    "p_false": 0.035,
    "p_anomaly": 0.02,
    "p_cross_site_entity": 0.05,
    "p_notable": 0.012,
    "patterns_per_10k_users": 20.0,
    "min_patterns": 40,
    "decoy_ratio": 1.25,      # decoys per real pattern
    "rare_fraction": 0.40,   # fraction of real patterns that are rare-signal
    "facet_support_common": (3, 9),   # users per facet
    "facet_support_rare": (1, 2),
    "pattern_len_choices": (3, 4, 5),
    "p_pattern_contradicted": 0.25,
    "variant_family_fraction": 0.35,
    "dup_inflation_users": (12, 40),  # D4 decoys echoed by this many users
}


def _mk_entities(rng: np.random.Generator, n: int) -> Tuple[List[str], np.ndarray]:
    names, kinds, seen = [], [], set()
    i = 0
    while len(names) < n:
        stem = ENTITY_STEMS[i % len(ENTITY_STEMS)]
        suf = ENTITY_SUFFIX[(i // len(ENTITY_STEMS)) % len(ENTITY_SUFFIX)]
        extra = "" if i < len(ENTITY_STEMS) * len(ENTITY_SUFFIX) else str(i)
        nm = stem + suf + extra
        if nm not in seen:
            seen.add(nm)
            names.append(nm)
            kinds.append(i % len(ENTITY_KINDS))
        i += 1
    return names, np.array(kinds, dtype=np.int8)


def _near_miss(entities: List[str], eid: int, rng: np.random.Generator,
               pool: List[int]) -> int:
    """Pick a different entity whose name shares the stem (lexically similar)."""
    base = entities[eid]
    stem = base.rstrip("0123456789").split("-")[0]
    cands = [j for j in pool if j != eid and entities[j].startswith(stem[:5])]
    if cands:
        return int(cands[rng.integers(0, len(cands))])
    return int(pool[rng.integers(0, len(pool))])


def build_corpus(org: Org, seed: int = 0,
                 cfg: Optional[Dict[str, object]] = None) -> Corpus:
    c = dict(DEFAULT_CFG)
    if cfg:
        c.update(cfg)
    rng = np.random.default_rng(1_000_003 + seed)
    users = org.user_ids
    nu = len(users)
    uidx = {u: i for i, u in enumerate(users)}
    n_days = int(c["n_days"])

    n_ent = max(int(c["min_entities"]),
                int(c["entities_per_1k_users"] * nu / 1000))
    entities, ent_kind = _mk_entities(rng, n_ent)

    # ---- per-user record budget ----
    mu = np.log(float(c["recs_per_user_median"]))
    cnt = np.clip(np.rint(rng.lognormal(mu, float(c["recs_per_user_sigma"]), nu)),
                  int(c["recs_per_user_lo"]), int(c["recs_per_user_hi"])).astype(np.int64)
    total_bg = int(cnt.sum())

    # Entity affinity.  A small GLOBAL pool (shared platforms, corporate
    # vendors) is visible everywhere; the rest of the entity namespace is
    # PARTITIONED into site-private blocks (a component, tool or supplier used
    # at one location).  This is what makes "the same entity showing up in
    # three regions" an actual anomaly rather than the norm, and therefore
    # what makes hierarchical index routing informative at all.
    n_global = max(6, n_ent // 12)
    global_pool = list(range(0, n_global))
    site_ids = org.levels[SITE]
    private = list(range(n_global, n_ent))
    rng.shuffle(private)
    site_pool: Dict[int, List[int]] = {}
    per = max(6, len(private) // max(1, len(site_ids)))
    for i, s in enumerate(site_ids):
        blk = private[i * per:(i + 1) * per]
        if len(blk) < 6:
            blk = private[-per:]
        site_pool[s] = list(blk) + list(rng.choice(
            np.array(global_pool), size=min(4, n_global), replace=False).astype(int))
    private_pool = private
    user_site = np.array([org.ancestor_at(u, SITE) for u in users])
    user_region = np.array([org.ancestor_at(u, REGION) for u in users])

    # ---------------- background records ----------------
    recs = np.zeros(total_bg, dtype=REC_DTYPE)
    off = np.zeros(nu + 1, dtype=np.int64)
    off[1:] = np.cumsum(cnt)
    bg_ids = np.array([PRED_ID[p] for p in BACKGROUND_PREDICATES])
    recs["uid"] = np.repeat(np.array(users, dtype=np.int32), cnt)
    recs["t"] = rng.integers(0, n_days, total_bg).astype(np.int16)
    # most background records use background predicates; a slice uses causal
    # predicates too (so causal predicates are NOT a give-away signal)
    use_causal = rng.random(total_bg) < 0.22
    recs["pred"] = np.where(use_causal,
                            rng.integers(0, N_CAUSAL_PRED, total_bg),
                            bg_ids[rng.integers(0, len(bg_ids), total_bg)]
                            ).astype(np.int16)
    # anchors: 70% site-local pool, 20% global pool, 10% anywhere
    anchors = np.empty(total_bg, dtype=np.int32)
    which = rng.random(total_bg)
    site_of_rec = np.repeat(user_site, cnt)
    for s in site_ids:
        m = site_of_rec == s
        if not m.any():
            continue
        pool = np.array(site_pool[s], dtype=np.int32)
        k = int(m.sum())
        anchors[m] = pool[rng.integers(0, len(pool), k)]
    gm = (which >= 0.70) & (which < 0.86)
    gpool = np.array(global_pool, dtype=np.int32)
    anchors[gm] = gpool[rng.integers(0, len(gpool), int(gm.sum()))]
    # Benign cross-site traffic: shared suppliers, transferred staff, joint
    # projects, M&A overlap.  Without this, the only entities visible in more
    # than one region would be the planted patterns, and index-based triage
    # would be a giveaway rather than a weak signal.
    xm = (which >= 0.86) & (which < 0.86 + float(c["p_cross_site_entity"]))
    if xm.any():
        pools = [np.array(site_pool[s2], dtype=np.int32) for s2 in site_ids]
        pick_site = rng.integers(0, len(site_ids), int(xm.sum()))
        vals = np.array([pools[int(ps)][int(rng.integers(0, len(pools[int(ps)])))]
                         for ps in pick_site], dtype=np.int32)
        anchors[xm] = vals
    am = which >= 1.01  # disabled: entity namespace is partitioned by site
    anchors[am] = rng.integers(0, n_ent, int(am.sum())).astype(np.int32)
    recs["anchor"] = anchors
    recs["aux"] = np.where(rng.random(total_bg) < 0.5,
                           rng.integers(0, n_ent, total_bg), -1).astype(np.int32)
    recs["event"] = np.arange(total_bg, dtype=np.int32)
    recs["kind"] = KIND_ROUTINE
    recs["veracity"] = 1
    recs["polarity"] = 1
    recs["group"] = -1
    recs["facet"] = -1
    recs["salience"] = rng.random(total_bg).astype(np.float32) * 0.25

    # ---- mark duplicates / stale / false / anomaly / notable in-place ----
    r = rng.random(total_bg)
    dup_m = r < float(c["p_dup"])
    stale_m = (r >= float(c["p_dup"])) & (r < float(c["p_dup"]) + float(c["p_stale"]))
    base2 = float(c["p_dup"]) + float(c["p_stale"])
    false_m = (r >= base2) & (r < base2 + float(c["p_false"]))
    base3 = base2 + float(c["p_false"])
    anom_m = (r >= base3) & (r < base3 + float(c["p_anomaly"]))
    base4 = base3 + float(c["p_anomaly"])
    notab_m = (r >= base4) & (r < base4 + float(c["p_notable"]))

    # Echoes circulate locally, not globally: a duplicate copies a record from
    # the SAME SITE.  (Global copying would smear every entity across every
    # region and destroy the locality structure the index depends on.)
    site_rec = np.repeat(user_site, cnt)
    sorder = np.argsort(site_rec, kind="stable")
    svals, sstart, scount = np.unique(site_rec[sorder], return_index=True,
                                      return_counts=True)
    site_slot = {int(v): (int(a), int(b)) for v, a, b in
                 zip(svals, sstart, scount)}

    def local_src(idx: np.ndarray) -> np.ndarray:
        out = np.empty(len(idx), dtype=np.int64)
        for i, r_ in enumerate(idx.tolist()):
            a, b = site_slot[int(site_rec[r_])]
            out[i] = sorder[a + int(rng.integers(0, b))]
        return out

    dup_idx = np.nonzero(dup_m)[0]
    src = local_src(dup_idx)
    recs["kind"][dup_idx] = KIND_DUP
    recs["pred"][dup_idx] = recs["pred"][src]
    recs["anchor"][dup_idx] = recs["anchor"][src]
    recs["event"][dup_idx] = recs["event"][src]      # SAME event id -> not independent
    recs["t"][dup_idx] = np.clip(recs["t"][src] + rng.integers(0, 6, len(dup_idx)),
                                 0, n_days - 1).astype(np.int16)

    st_idx = np.nonzero(stale_m)[0]
    recs["kind"][st_idx] = KIND_STALE
    recs["superseded"][st_idx] = 1
    recs["t"][st_idx] = rng.integers(0, max(1, n_days // 3), len(st_idx)).astype(np.int16)

    fl_idx = np.nonzero(false_m)[0]
    recs["kind"][fl_idx] = KIND_FALSE
    recs["veracity"][fl_idx] = 0
    recs["polarity"][fl_idx] = -1

    an_idx = np.nonzero(anom_m)[0]
    recs["kind"][an_idx] = KIND_ANOMALY
    recs["salience"][an_idx] = 0.45 + rng.random(len(an_idx)).astype(np.float32) * 0.2

    nt_idx = np.nonzero(notab_m)[0]
    recs["kind"][nt_idx] = KIND_NOTABLE
    recs["salience"][nt_idx] = 0.7 + rng.random(len(nt_idx)).astype(np.float32) * 0.3

    # contradiction pairs: for each false record, find/emit a true counterpart
    contradiction_pairs: List[Tuple[int, int]] = []
    if len(fl_idx):
        partner = local_src(fl_idx)
        for f, p in zip(fl_idx.tolist(), partner.tolist()):
            if recs["uid"][f] == recs["uid"][p]:
                continue
            recs["pred"][f] = recs["pred"][p]
            recs["anchor"][f] = recs["anchor"][p]
            recs["event"][f] = total_bg + f   # distinct event: a genuine conflict
            contradiction_pairs.append((int(p), int(f)))

    # ---------------- strategic patterns ----------------
    n_pat = max(int(c["min_patterns"]),
                int(round(float(c["patterns_per_10k_users"]) * nu / 10_000)))
    n_decoy = int(round(n_pat * float(c["decoy_ratio"])))
    extra: List[np.ndarray] = []
    patterns: List[Pattern] = []
    next_event = total_bg * 2 + 10
    region_ids = org.levels[REGION]
    users_by_region: Dict[int, np.ndarray] = {}
    for rid_ in region_ids:
        m = np.nonzero(user_region == rid_)[0]
        users_by_region[rid_] = m

    def pick_users(k: int, prefer_regions: Sequence[int]) -> List[int]:
        out: List[int] = []
        for i in range(k):
            reg = prefer_regions[i % len(prefer_regions)]
            pool = users_by_region[reg]
            if len(pool) == 0:
                pool = np.arange(nu)
            out.append(int(pool[rng.integers(0, len(pool))]))
        return out

    # we need real record ids, so collect (rows, meta) then assign ids at the end
    pending: List[Tuple[np.ndarray, Pattern, int]] = []

    # Anchors are drawn WITHOUT replacement across real patterns and decoys.
    # Sharing an entity between a real pattern and a decoy is realistic, but it
    # makes one reported hypothesis simultaneously a correct discovery and a
    # decoy acceptance, which is a scoring ambiguity rather than an
    # interesting phenomenon.  Measured at ~10% of real patterns before this
    # change.
    _used_anchors: set = set()

    def _take_anchor() -> int:
        for _ in range(500):
            a = (int(private_pool[rng.integers(0, len(private_pool))])
                 if rng.random() < 0.8 else int(rng.integers(0, n_global)))
            if a not in _used_anchors:
                _used_anchors.add(a)
                return a
        a = int(rng.integers(0, n_ent))
        _used_anchors.add(a)
        return a

    rare_flags = rng.random(n_pat) < float(c["rare_fraction"])
    fam_flags = rng.random(n_pat) < float(c["variant_family_fraction"])
    families: Dict[int, List[int]] = {}
    fam_next = 0
    fam_open: List[Tuple[int, int, int]] = []   # (family, chain, start_pos)

    for pi in range(n_pat):
        rare = bool(rare_flags[pi])
        ci = int(rng.integers(0, len(CAUSAL_CHAINS)))
        chain = CAUSAL_CHAINS[ci]
        L = int(rng.choice(np.array(c["pattern_len_choices"])))
        L = min(L, len(chain))
        sp = int(rng.integers(0, len(chain) - L + 1))
        fam = -1
        if fam_open and rng.random() < 0.55:
            fam, ci, sp = fam_open[int(rng.integers(0, len(fam_open)))]
            chain = CAUSAL_CHAINS[ci]
            L = min(L, len(chain) - sp)
        elif fam_flags[pi]:
            fam = fam_next
            fam_next += 1
            fam_open.append((fam, ci, sp))
        preds = [PRED_ID[chain[sp + j]] for j in range(L)]
        anchor = _take_anchor()
        # scatter facets across regions: at least 2 distinct regions
        k_reg = min(len(region_ids), 2 + int(rng.integers(0, 3)))
        regs = list(rng.choice(np.array(region_ids), size=k_reg, replace=False).astype(int))
        lo, hi = (c["facet_support_rare"] if rare else c["facet_support_common"])
        t0 = int(rng.integers(0, max(1, n_days - 20 * L)))
        pat = Pattern(pid=len(patterns), chain=ci, start_pos=sp, preds=preds,
                      anchor=anchor, facet_records=[], facet_users=[],
                      facet_times=[], real=True, rare=rare, family=fam)
        for j, pr in enumerate(preds):
            k = int(rng.integers(lo, hi + 1))
            us = pick_users(k, [regs[j % len(regs)]])
            t = min(n_days - 1, t0 + j * int(rng.integers(4, 18)))
            rows = np.zeros(k, dtype=REC_DTYPE)
            rows["uid"] = np.array([users[i] for i in us], dtype=np.int32)
            rows["t"] = t
            rows["pred"] = pr
            rows["anchor"] = anchor
            rows["aux"] = -1
            rows["event"] = np.arange(next_event, next_event + k, dtype=np.int32)
            next_event += k
            rows["kind"] = KIND_FACET
            rows["veracity"] = 1
            rows["polarity"] = 1
            rows["group"] = pat.pid
            rows["facet"] = j
            rows["salience"] = 0.30 + 0.1 * rng.random(k).astype(np.float32)
            pending.append((rows, pat, j))
            pat.facet_users.append([users[i] for i in us])
            pat.facet_times.append(t)
        if rng.random() < float(c["p_pattern_contradicted"]):
            pat.contradicted = True
            j = int(rng.integers(0, L))
            us = pick_users(1, [regs[j % len(regs)]])
            rows = np.zeros(1, dtype=REC_DTYPE)
            rows["uid"] = np.array([users[us[0]]], dtype=np.int32)
            rows["t"] = min(n_days - 1, pat.facet_times[j] + 3)
            rows["pred"] = preds[j]
            rows["anchor"] = anchor
            rows["aux"] = -1
            rows["event"] = next_event
            next_event += 1
            rows["kind"] = KIND_FACET
            rows["veracity"] = 0
            rows["polarity"] = -1
            rows["group"] = pat.pid
            rows["facet"] = j
            rows["salience"] = 0.35
            pending.append((rows, pat, j))
        if fam >= 0:
            families.setdefault(fam, []).append(pat.pid)
        patterns.append(pat)

    # ---------------- decoys ----------------
    all_pool = list(range(n_ent))
    for di in range(n_decoy):
        dtype = 1 + (di % 5)
        L = int(rng.choice(np.array(c["pattern_len_choices"])))
        ci = int(rng.integers(0, len(CAUSAL_CHAINS)))
        chain = CAUSAL_CHAINS[ci]
        L = min(L, len(chain))
        sp = int(rng.integers(0, len(chain) - L + 1))
        anchor = _take_anchor()
        k_reg = min(len(region_ids), 2 + int(rng.integers(0, 3)))
        regs = list(rng.choice(np.array(region_ids), size=k_reg, replace=False).astype(int))
        t0 = int(rng.integers(0, max(1, n_days - 20 * L)))
        pat = Pattern(pid=len(patterns), chain=ci, start_pos=sp, preds=[],
                      anchor=anchor, facet_records=[], facet_users=[],
                      facet_times=[], real=False, decoy_type=dtype)
        if dtype == 1:      # entity coincidence: predicates from mixed chains
            preds = []
            for _ in range(L):
                cj = int(rng.integers(0, len(CAUSAL_CHAINS)))
                preds.append(PRED_ID[CAUSAL_CHAINS[cj][
                    int(rng.integers(0, len(CAUSAL_CHAINS[cj])))]])
            times = [int(rng.integers(0, n_days)) for _ in range(L)]
            anchors = [anchor] * L
        elif dtype == 2:    # temporal scramble
            preds = [PRED_ID[chain[sp + j]] for j in range(L)]
            times = sorted([min(n_days - 1, t0 + j * int(rng.integers(4, 18)))
                            for j in range(L)], reverse=True)
            anchors = [anchor] * L
        elif dtype == 3:    # near-miss entities
            preds = [PRED_ID[chain[sp + j]] for j in range(L)]
            times = [min(n_days - 1, t0 + j * int(rng.integers(4, 18))) for j in range(L)]
            anchors = [anchor] + [_near_miss(entities, anchor, rng, all_pool)
                                  for _ in range(L - 1)]
        elif dtype == 4:    # duplicate inflation (one event, many echoes)
            preds = [PRED_ID[chain[sp + j]] for j in range(L)]
            times = [min(n_days - 1, t0 + j * int(rng.integers(4, 18))) for j in range(L)]
            anchors = [anchor] * L
        else:               # dtype == 5: stale chain - every facet later retracted
            preds = [PRED_ID[chain[sp + j]] for j in range(L)]
            times = [min(n_days // 2, t0 % max(1, n_days // 2) + j * int(rng.integers(3, 9)))
                     for j in range(L)]
            anchors = [anchor] * L
        pat.preds = preds
        for j, pr in enumerate(preds):
            if dtype == 4:
                lo4, hi4 = c["dup_inflation_users"]
                k = int(rng.integers(lo4, hi4 + 1))
            else:
                lo, hi = (c["facet_support_common"])
                k = int(rng.integers(lo, hi + 1))
            us = pick_users(k, regs)
            rows = np.zeros(k, dtype=REC_DTYPE)
            rows["uid"] = np.array([users[i] for i in us], dtype=np.int32)
            rows["t"] = times[j]
            rows["pred"] = pr
            rows["anchor"] = anchors[j]
            rows["aux"] = -1
            if dtype == 4:
                rows["event"] = next_event      # ONE event id for all echoes
                rows["kind"] = KIND_DUP
                next_event += 1
            else:
                rows["event"] = np.arange(next_event, next_event + k, dtype=np.int32)
                rows["kind"] = KIND_DECOY
                next_event += k
            rows["veracity"] = 1
            rows["polarity"] = 1
            rows["group"] = -2 - pat.pid
            rows["facet"] = j
            rows["salience"] = 0.30 + 0.1 * rng.random(k).astype(np.float32)
            pending.append((rows, pat, j))
            pat.facet_users.append([users[i] for i in us])
            pat.facet_times.append(times[j])
            if dtype == 5:
                # a later, independent retraction of the same claim
                kr = max(1, k // 2)
                ur = pick_users(kr, regs)
                rr = np.zeros(kr, dtype=REC_DTYPE)
                rr["uid"] = np.array([users[i] for i in ur], dtype=np.int32)
                rr["t"] = min(n_days - 1, times[j] + int(rng.integers(30, 70)))
                rr["pred"] = pr
                rr["anchor"] = anchors[j]
                rr["aux"] = -1
                rr["event"] = np.arange(next_event, next_event + kr, dtype=np.int32)
                next_event += kr
                rr["kind"] = KIND_DECOY
                rr["veracity"] = 1
                rr["polarity"] = -1
                rr["group"] = -2 - pat.pid
                rr["facet"] = j
                rr["salience"] = 0.3
                pending.append((rr, pat, j))
        patterns.append(pat)

    # ---------------- assemble ----------------
    blocks = [recs] + [p[0] for p in pending]
    all_recs = np.concatenate(blocks)
    base = len(recs)
    cursor = base
    for rows, pat, j in pending:
        ids = list(range(cursor, cursor + len(rows)))
        while len(pat.facet_records) <= j:
            pat.facet_records.append([])
        pat.facet_records[j].extend(ids)
        cursor += len(rows)

    # sort by uid so per-user slices are contiguous
    order = np.argsort(all_recs["uid"], kind="stable")
    inv = np.empty(len(order), dtype=np.int64)
    inv[order] = np.arange(len(order))
    all_recs = all_recs[order]
    for pat in patterns:
        pat.facet_records = [[int(inv[r]) for r in fr] for fr in pat.facet_records]
    contradiction_pairs = [(int(inv[a]), int(inv[b])) for a, b in contradiction_pairs]
    notable = [int(inv[i]) for i in nt_idx.tolist()]

    slices = np.zeros(nu + 1, dtype=np.int64)
    uu = all_recs["uid"]
    counts = np.bincount(np.array([uidx[int(x)] for x in uu], dtype=np.int64),
                         minlength=nu)
    slices[1:] = np.cumsum(counts)

    # spread stats for patterns
    for pat in patterns:
        regs = set()
        sites = set()
        for fr in pat.facet_records:
            for rid_ in fr:
                u = int(all_recs["uid"][rid_])
                regs.add(org.ancestor_at(u, REGION))
                sites.add(org.ancestor_at(u, SITE))
        pat.n_regions = len(regs)
        pat.n_sites = len(sites)

    dup_events: Dict[int, List[int]] = {}
    return Corpus(org=org, recs=all_recs, user_slices=slices, patterns=patterns,
                  entities=entities, entity_kind=ent_kind, n_days=n_days,
                  seed=seed, cfg=c, notable_records=notable, families=families,
                  contradiction_pairs=contradiction_pairs, dup_events=dup_events)


# --------------------------------------------------------------------------
# Ground truth / gold answers
# --------------------------------------------------------------------------

@dataclass
class Gold:
    discoverable: List[int]        # real pattern ids that a perfect system finds
    rare: List[int]
    contradicted: List[int]
    families: Dict[int, List[int]]
    decoys: List[int]
    evidence: Dict[int, List[int]]     # pattern -> true supporting record ids
    independent_sources: Dict[int, int]
    notable_records: List[int]
    top_supported: int                 # pattern with most independent sources


def make_gold(corpus: Corpus, min_facets: int = 3) -> Gold:
    disc, rare, contra = [], [], []
    ev: Dict[int, List[int]] = {}
    ind: Dict[int, int] = {}
    for p in corpus.patterns:
        if not p.real:
            continue
        present = sum(1 for fr in p.facet_records if fr)
        if present < min(min_facets, len(p.preds)):
            continue
        disc.append(p.pid)
        rows = [r for fr in p.facet_records for r in fr]
        ev[p.pid] = rows
        ind[p.pid] = len({int(corpus.recs["event"][r]) for r in rows})
        if p.rare:
            rare.append(p.pid)
        if p.contradicted:
            contra.append(p.pid)
    fams = {f: [p for p in ps if p in set(disc)]
            for f, ps in corpus.families.items()}
    fams = {f: ps for f, ps in fams.items() if len(ps) >= 2}
    decoys = [p.pid for p in corpus.patterns if not p.real]
    top = max(ind, key=lambda k: ind[k]) if ind else -1
    return Gold(discoverable=disc, rare=rare, contradicted=contra, families=fams,
                decoys=decoys, evidence=ev, independent_sources=ind,
                notable_records=corpus.notable_records, top_supported=top)


if __name__ == "__main__":
    import json
    import time
    from .org import build_org
    for n in (1000, 10_000):
        t0 = time.time()
        o = build_org(n, seed=0)
        cp = build_corpus(o, seed=0)
        g = make_gold(cp)
        print(n, f"{time.time()-t0:.1f}s", json.dumps(cp.stats()))
        print("   gold:", len(g.discoverable), "rare", len(g.rare),
              "fams", len(g.families), "decoys", len(g.decoys))

"""Heterogeneous synthetic enterprise org-chart generator.

Six levels, USER(0) -> TEAM(1) -> DEPARTMENT(2) -> SITE/SUBSIDIARY(3) ->
REGION(4) -> ENTERPRISE(5).

Fan-in is drawn from heavy-tailed distributions clipped to plausible ranges so
the tree is genuinely heterogeneous (small teams next to large ones, small
satellite offices next to major sites), not a balanced k-ary tree.

Everything is deterministic given (seed, target_users, knobs).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

LEVEL_NAMES = ["user", "team", "department", "site", "region", "enterprise"]
USER, TEAM, DEPT, SITE, REGION, ENT = range(6)


@dataclass
class Node:
    nid: int
    level: int
    parent: Optional[int]
    children: List[int] = field(default_factory=list)
    name: str = ""
    # set for every node: the leaf user ids beneath it (only materialised for
    # levels >= 1 on demand)
    n_users: int = 0
    # index of the region / site / dept / team ancestor (self at own level)
    anc: Tuple[int, ...] = ()
    function: int = 0  # functional family (eng, ops, sales, finance, ...)


N_FUNCTIONS = 8
FUNCTION_NAMES = [
    "engineering", "operations", "supply-chain", "sales",
    "finance", "security", "support", "manufacturing",
]


def _clipped_lognormal(rng: np.random.Generator, n: int, mean: float,
                       sigma: float, lo: int, hi: int) -> np.ndarray:
    """n draws with median ~mean, clipped to [lo, hi], integer."""
    mu = math.log(mean)
    vals = rng.lognormal(mu, sigma, size=n)
    return np.clip(np.rint(vals), lo, hi).astype(np.int64)


@dataclass
class Org:
    nodes: List[Node]
    levels: List[List[int]]          # levels[l] = node ids at level l
    root: int
    seed: int
    # convenience
    user_ids: List[int] = field(default_factory=list)

    def parent_of(self, nid: int) -> Optional[int]:
        return self.nodes[nid].parent

    def ancestors(self, nid: int) -> List[int]:
        out = []
        p = self.nodes[nid].parent
        while p is not None:
            out.append(p)
            p = self.nodes[p].parent
        return out

    def ancestor_at(self, nid: int, level: int) -> Optional[int]:
        n = self.nodes[nid]
        if n.level == level:
            return nid
        for a in self.ancestors(nid):
            if self.nodes[a].level == level:
                return a
        return None

    def leaves_under(self, nid: int) -> List[int]:
        n = self.nodes[nid]
        if n.level == 0:
            return [nid]
        out: List[int] = []
        stack = [nid]
        while stack:
            c = stack.pop()
            nc = self.nodes[c]
            if nc.level == 0:
                out.append(c)
            else:
                stack.extend(nc.children)
        return out

    def stats(self) -> Dict[str, object]:
        s: Dict[str, object] = {"n_users": len(self.user_ids), "seed": self.seed}
        for l in range(1, 6):
            fan = [len(self.nodes[i].children) for i in self.levels[l]]
            if not fan:
                continue
            s[LEVEL_NAMES[l]] = {
                "count": len(self.levels[l]),
                "fanin_mean": round(float(np.mean(fan)), 2),
                "fanin_p10": int(np.percentile(fan, 10)),
                "fanin_p50": int(np.percentile(fan, 50)),
                "fanin_p90": int(np.percentile(fan, 90)),
                "fanin_min": int(np.min(fan)),
                "fanin_max": int(np.max(fan)),
            }
        usz = [self.nodes[i].n_users for i in self.levels[SITE]]
        s["site_user_counts"] = {
            "min": int(np.min(usz)), "p50": int(np.percentile(usz, 50)),
            "max": int(np.max(usz)),
        }
        rsz = [self.nodes[i].n_users for i in self.levels[REGION]]
        s["region_user_counts"] = sorted(int(x) for x in rsz)
        return s


# Default heterogeneous fan-in spec. (median, sigma, lo, hi) per level
# transition. sigma controls heterogeneity; 0 would give a balanced tree.
DEFAULT_FANIN = {
    TEAM: (9.0, 0.32, 4, 22),     # users per team  (median 9, range 4..22)
    DEPT: (7.0, 0.45, 3, 18),     # teams per department
    SITE: (5.0, 0.60, 1, 20),     # departments per site
    REGION: (6.0, 0.70, 1, 26),   # sites per region
    ENT: (7.0, 0.0, 5, 9),        # regions per enterprise (fixed-ish)
}


def build_org(target_users: int = 50_000, seed: int = 0,
              fanin: Optional[Dict[int, Tuple[float, float, int, int]]] = None,
              n_regions: int = 7) -> Org:
    """Grow bottom-up so the user count lands near the target.

    We build the tree top-down structurally but size it by repeatedly drawing
    fan-ins until the accumulated user count reaches the target, then stop.
    Region sizes are deliberately unequal (a big home region, several mid
    regions, a couple of small emerging-market regions).
    """
    rng = np.random.default_rng(seed)
    fanin = dict(DEFAULT_FANIN if fanin is None else fanin)

    nodes: List[Node] = []
    levels: List[List[int]] = [[] for _ in range(6)]

    def add(level: int, parent: Optional[int], name: str, function: int = 0) -> int:
        nid = len(nodes)
        nodes.append(Node(nid=nid, level=level, parent=parent, name=name,
                          function=function))
        levels[level].append(nid)
        if parent is not None:
            nodes[parent].children.append(nid)
        return nid

    root = add(ENT, None, "ENTERPRISE")

    # Region weights: deliberately skewed (Zipf-ish), so one region holds ~30%
    # of the company and the smallest holds ~3%.
    w = np.array([1.0 / (i + 1) ** 0.85 for i in range(n_regions)])
    w = w / w.sum()
    region_targets = np.maximum(40, np.rint(w * target_users)).astype(int)

    for ri in range(n_regions):
        rid = add(REGION, root, f"REGION-{ri:02d}")
        remaining = int(region_targets[ri])
        si = 0
        while remaining > 0:
            # site size heterogeneity: mixture of satellite offices and hubs
            u = rng.random()
            if u < 0.45:
                site_target = int(rng.integers(40, 220))        # satellite
            elif u < 0.85:
                site_target = int(rng.integers(220, 900))       # regular site
            else:
                site_target = int(rng.integers(900, 4200))      # major hub
            site_target = min(site_target, remaining) if remaining > 60 else remaining
            sid = add(SITE, rid, f"R{ri:02d}-SITE-{si:02d}")
            si += 1
            srem = site_target
            di = 0
            while srem > 0:
                # department = a functional family at this site
                fn = int(rng.integers(0, N_FUNCTIONS))
                did = add(DEPT, sid, f"R{ri:02d}S{si-1:02d}-DEPT-{di:02d}", function=fn)
                di += 1
                n_teams = int(_clipped_lognormal(rng, 1, *fanin[DEPT])[0])
                drem_target = srem
                built = 0
                for _t in range(n_teams):
                    if built >= drem_target:
                        break
                    tsz = int(_clipped_lognormal(rng, 1, *fanin[TEAM])[0])
                    tsz = min(tsz, max(1, drem_target - built))
                    tid = add(TEAM, did, f"{nodes[did].name}-T{_t:02d}", function=fn)
                    for k in range(tsz):
                        add(USER, tid, f"{nodes[tid].name}-U{k:02d}", function=fn)
                    built += tsz
                if built == 0:  # degenerate department, drop it
                    nodes[sid].children.remove(did)
                    levels[DEPT].remove(did)
                srem -= built
                if built == 0:
                    break
            remaining -= (site_target - max(0, srem))
            if site_target <= 0:
                break

    # propagate function labels upward (site/region = modal child function)
    for l in (SITE, REGION):
        for nid in levels[l]:
            fs = [nodes[c].function for c in nodes[nid].children]
            nodes[nid].function = int(np.bincount(fs).argmax()) if fs else 0

    # user counts + ancestor tuples
    user_ids = list(levels[USER])
    for nid in user_ids:
        nodes[nid].n_users = 1
    for l in range(1, 6):
        for nid in levels[l]:
            nodes[nid].n_users = sum(nodes[c].n_users for c in nodes[nid].children)

    for nid in range(len(nodes)):
        n = nodes[nid]
        chain = [nid]
        p = n.parent
        while p is not None:
            chain.append(p)
            p = nodes[p].parent
        n.anc = tuple(chain)

    org = Org(nodes=nodes, levels=levels, root=root, seed=seed, user_ids=user_ids)
    return org


def rescale_fanin(base: Dict[int, Tuple[float, float, int, int]],
                  level: int, median: float) -> Dict[int, Tuple[float, float, int, int]]:
    out = dict(base)
    m, s, lo, hi = out[level]
    out[level] = (median, s, lo, hi)
    return out


if __name__ == "__main__":
    import json
    for tgt in (1000, 10_000, 50_000):
        o = build_org(tgt, seed=0)
        print(tgt, json.dumps(o.stats(), indent=None)[:600])

"""Shared gate model for H7 (mirrors R/tools/plateau/ceiling_b.py: K=3, WIN=6, FRESH=42, MIN_ROOTS=MIN_REP=3,
TB_v4 = N1 weak corroborator + N2 roots only from k-root sites, as_of(w) = sunday(w)+20d, freshness <= 42d, i.e.
w - newest <= 3 weeks). Buckets are the emergence pack's verdict_count_buckets [3,10,50]: '<k' | '3-9' | '10-49' | '50+'.

Polarity model, one parameter ALPHA per site in a window:
    ALPHA == 'blind' : records = pos + neg, roots/reporters over all records        (model B, the shipped judge)
    ALPHA == 0       : records = pos, roots/reporters over positive records         (model K: contradiction ignored)
    ALPHA > 0        : records = max(0, pos - ALPHA*neg), roots/reporters over positive records  (NET: inhibition)
    ALPHA == 'drop'  : a site holding any neg record is dropped                     (model Ks: strict)
Everything a statistic may read is the per-site exported shape: (support bucket, roots bucket, reporters bucket,
newest week) -> lower bounds via lb(). uid/event are read ONLY by dedupe_window() (the oracle for question A).
"""
import json, os
from collections import defaultdict

OUT = os.path.dirname(os.path.abspath(__file__))
K = 3; WIN = 6; MIN_ROOTS = 3; MIN_REP = 3; FRESH_WEEKS = 3
EDGES = (3, 10, 50)


def lb(n):
    """Exported lower bound of a count: 0 none, 1 for '<k', else the bucket floor (3, 10, 50)."""
    if n <= 0: return 0
    if n < K: return 1
    return max(e for e in EDGES if e <= n)


def bidx(n):
    """Bucket index as exported: 0 '<k', 1 '3-9', 2 '10-49', 3 '50+' (None when the site holds nothing)."""
    if n <= 0: return None
    return sum(1 for e in EDGES if e <= n)


def load(seed):
    return json.load(open(f"{OUT}/cache/seed-{seed}.json"))


def window_recs(recs, lo, hi):
    return [r for r in recs if lo <= r[0] <= hi]


def site_stats(wrecs, alpha):
    """Per-site exported-shape stats for the records of one window under the polarity model alpha.
    Returns {site: {records, users, events, newest, pos, neg}} with records/users/events already model-adjusted."""
    acc = defaultdict(lambda: {"pos": 0, "neg": 0, "users_all": set(), "events_all": set(), "users_pos": set(),
                               "events_pos": set(), "newest_all": -1, "newest_pos": -1})
    for wi, site, uid, ev, pol, _f in wrecs:
        a = acc[site]
        a["users_all"].add(uid); a["events_all"].add(ev); a["newest_all"] = max(a["newest_all"], wi)
        if pol < 0:
            a["neg"] += 1
        else:
            a["pos"] += 1; a["users_pos"].add(uid); a["events_pos"].add(ev); a["newest_pos"] = max(a["newest_pos"], wi)
    out = {}
    for s, a in acc.items():
        if alpha == "blind":
            rec, users, events, newest = a["pos"] + a["neg"], len(a["users_all"]), len(a["events_all"]), a["newest_all"]
        elif alpha == "drop":
            if a["neg"]: continue
            rec, users, events, newest = a["pos"], len(a["users_pos"]), len(a["events_pos"]), a["newest_pos"]
        else:
            rec = max(0, a["pos"] - alpha * a["neg"])
            users, events, newest = len(a["users_pos"]), len(a["events_pos"]), a["newest_pos"]
        if rec <= 0: continue
        out[s] = {"records": rec, "users": users, "events": events, "newest": newest, "pos": a["pos"], "neg": a["neg"]}
    return out


def tb_v4(st, w):
    """The Track-B v4 gate over exported lower bounds. Returns (passed, counted_sites, path) where path is
    '2k' (two k-sites), 'n1' (one k-site + one weak corroborator) or None."""
    ks = sorted((s for s, v in st.items() if v["records"] >= K), key=lambda s: (-st[s]["events"], -st[s]["users"], s))
    weak = [s for s, v in st.items() if 0 < v["records"] < K]
    def full(sel):
        if not sel: return False
        roots = sum(lb(st[s]["events"]) for s in sel if st[s]["events"] >= K)      # N2
        rep = sum(lb(st[s]["users"]) for s in sel)
        fresh = (w - max(st[s]["newest"] for s in sel)) <= FRESH_WEEKS
        return roots >= MIN_ROOTS and rep >= MIN_REP and fresh
    if len(ks) >= 2 and full(ks[:3]):
        return True, ks[:3], "2k"
    if len(ks) >= 1 and weak and full(ks[:2]):
        return True, ks[:2] + [sorted(weak)[0]], "n1"
    return False, [], None


def support_lb(st):
    """Gate-shaped graded support: sum over sites of the support bucket lower bound ('<k' counts 1)."""
    return sum(lb(v["records"]) for v in st.values())


def dedupe_window(wrecs, alpha):
    """ORACLE (question A): drop every record whose root (event) already appeared at a stronger site in this window,
    so that two sites echoing one root count as one. Strength order = the gate's own (records, events desc)."""
    st = site_stats(wrecs, alpha)
    order = sorted(st, key=lambda s: (-st[s]["records"], -st[s]["events"], s))
    seen = set(); kept = []
    bysite = defaultdict(list)
    for r in wrecs: bysite[r[1]].append(r)
    for s in order:
        for r in bysite[s]:
            if r[3] in seen: continue
            kept.append(r)
        seen.update(r[3] for r in bysite[s])
    return kept


def cross_site_shared_events(wrecs, sites):
    """ORACLE: events held at more than one of `sites` in this window."""
    ev_sites = defaultdict(set)
    for r in wrecs:
        if r[1] in sites: ev_sites[r[3]].add(r[1])
    return {e for e, ss in ev_sites.items() if len(ss) > 1}


def cross_site_shared_users(wrecs):
    us = defaultdict(set)
    for r in wrecs: us[r[2]].add(r[1])
    return {u for u, ss in us.items() if len(ss) > 1}


def auc(pos, neg):
    """Mann-Whitney AUC with ties at 0.5."""
    if not pos or not neg: return float("nan")
    allv = sorted(set(pos) | set(neg))
    import bisect
    neg_s = sorted(neg)
    tot = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg_s, p); hi = bisect.bisect_right(neg_s, p)
        tot += lo + 0.5 * (hi - lo)
    return tot / (len(pos) * len(neg))


def precision_at(scores, labels, n):
    """Expected #labels==True in the top n of a ranking by score desc, ties split proportionally."""
    groups = defaultdict(lambda: [0, 0])
    for s, l in zip(scores, labels):
        groups[s][0] += 1; groups[s][1] += int(l)
    got = 0.0; left = n
    for s in sorted(groups, reverse=True):
        size, g = groups[s]
        if left <= 0: break
        take = min(size, left)
        got += g * take / size; left -= take
    return got

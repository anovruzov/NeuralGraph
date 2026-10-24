"""Recursive acquisition, measured offline against a budget-matched untargeted control (dev seeds only).

ESTIMATE, not a run: it reuses ``ceiling_b``'s gate model (the same ``site_stats``/``gates`` the ceilings use)
over the frozen generator, so it answers "would the shipped gate have cleared this key after the extra round"
without an e2e run.  It never reads a gold label to choose what to ask -- labels are used only to score.

The loop, per the mission's spec:

  DETECT          every key the detector could carry (anchor, chain predicate) with >= 1 record
  MISSING SLOTS   from the *exported contract* only: each site reports its count bucketed at k, so a site
                  holding 1..k-1 records exports ``'<k'``.  A key whose buckets fail TB_v4 but show at least
                  one ``'<k'`` site is one disclosure short -- that is the missing slot.  No private state,
                  no node ids, no value equality, no gold.
  SELECT HOLDERS  up to MAX_HOLDERS of those sites, strongest bucket first, site id as the tie-break
  INQUIRE         the holder discloses what it has on the key outside the 6-week window -- up to MAX_CLAIMS
                  records, oldest first.  This is the whole acquisition: the records exist at the site and the
                  windowed count gate never saw them
  VERIFY          recount with the inquired sites' window widened, re-apply TB_v4.  MAX_VERIFY rounds
  STOP            when the gate clears, the budget is spent, or a round returns nothing new

The control arm spends the identical budget (same keys, same MAX_HOLDERS/MAX_CLAIMS/MAX_VERIFY, same widening)
but picks holders without the missing-slot signal: a seeded shuffle of the org's sites.  If targeting does not
beat that, the signal is not doing the work.

Usage:  PYTHONPATH=$EMERGENCE_RULE python3 tools/plateau/recursive_acquire.py <out.json> [seeds]
"""
import json, os, random, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ceiling_b import site_stats, gates, K, WIN
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES

ew.check_world_counts = lambda *a, **k: None

MAX_HOLDERS = 3          # holders inquired per key, per the agreed budget
MAX_CLAIMS = 8           # records a holder may disclose in one inquiry
MAX_VERIFY = 2           # verification rounds


def exported_buckets(st):
    """What HQ sees: each site's count bucketed at k.  ``'<k'`` for 1..k-1, the count itself at or above k."""
    return {s: (v["records"] if v["records"] >= K else "<k") for s, v in st.items() if v["records"] > 0}


def missing_slot_sites(buckets):
    """Sites one disclosure short, strongest bucket first then site id.  Reads the exported contract only."""
    return sorted((s for s, b in buckets.items() if b == "<k"), key=lambda s: s)


def widened(rows, recs, sites, widx, lo, hi, inquired, claims):
    """``site_stats`` with the inquired sites' window widened to everything up to ``hi``, each capped at
    ``claims`` extra records (oldest first), every other site unchanged."""
    keep = []
    extra = Counter()
    for r in rows:
        wi = widx[ew.iso_week(ew.received_date(int(recs[r]["t"])))]
        s = sites[int(recs[r]["uid"])]
        if lo <= wi <= hi:
            keep.append(r)
        elif wi < lo and s in inquired and extra[s] < claims:
            keep.append(r); extra[s] += 1
    return keep, sum(extra.values())


def run_key(rows, recs, sites, widx, weeks, lo_hi, pick):
    """The budget is per key, not per week: find the one week worth spending it on, spend MAX_HOLDERS holders x
    MAX_CLAIMS claims x MAX_VERIFY rounds there, and stop.  Returns
    (cleared_before, cleared_after, asks, claims, rounds, spent_week)."""
    lo_w, hi_w = lo_hi
    span = [w for w in range(WIN - 1, len(weeks)) if lo_w <= weeks[w] <= hi_w]
    for w in span:
        if gates(site_stats(rows, recs, sites, widx, w - WIN + 1, w), w, weeks, False)["TB_v4"]:
            return True, True, 0, 0, 0, weeks[w]
    # DETECT found nothing in any week.  Pick the single week to spend on: the one the exported contract says is
    # closest to clearing -- most sites reporting at all, then most at ``'<k'``, then latest.  Exported buckets
    # only; no gold, no private state.
    best, best_rank = None, None
    for w in span:
        st = site_stats(rows, recs, sites, widx, w - WIN + 1, w)
        b = exported_buckets(st)
        rank = (len(b), sum(1 for v in b.values() if v == "<k"), w)
        if b and (best_rank is None or rank > best_rank):
            best, best_rank = w, rank
    if best is None:
        return False, False, 0, 0, 0, None
    w, lo = best, best - WIN + 1
    st = site_stats(rows, recs, sites, widx, lo, w)
    holders = pick(exported_buckets(st), st)[:MAX_HOLDERS]
    if not holders:
        return False, False, 0, 0, 0, weeks[w]
    asks, claims, rounds, after, seen = len(holders), 0, 0, False, None
    for _ in range(MAX_VERIFY):
        rounds += 1
        got, n = widened(rows, recs, sites, widx, lo, w, set(holders), MAX_CLAIMS)
        if seen == n:                                        # a round that returns nothing new: stop
            break
        claims, seen = n, n
        if gates(site_stats(got, recs, sites, widx, 0, w), w, weeks, False)["TB_v4"]:
            after = True
            break
    return False, after, asks, claims, rounds, weeks[w]


def main(out_path, seeds):
    results = {}
    for seed in seeds:
        org, corpus, gold = ew.generate(200, seed)
        sites = ew._user_sites(org)
        labels = ew.gold_labels(corpus, gold)
        weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS))
        widx = {x: i for i, x in enumerate(weeks)}
        recs = corpus.recs
        bykey = defaultdict(list)
        for r in range(len(recs)):
            pr = int(recs[r]["pred"])
            pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
            if pp is None:
                continue
            for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
                if e >= 0:
                    bykey[(e, pp)].append(r)
        goldkey = {(int(corpus.patterns[l["pattern"]].anchor), CHAIN_PREDICATES[corpus.patterns[l["pattern"]].chain]):
                   l for l in labels["patterns"]}
        decoykey = {}
        for d in labels["decoys"]:
            p = corpus.patterns[int(d["pattern"])]
            if p.facet_records and p.facet_records[0]:
                decoykey[(int(recs[int(p.facet_records[0][0])]["anchor"]),
                          CHAIN_PREDICATES[int(p.chain)])] = d
        chain = set(CHAIN_PREDICATES.values()) if isinstance(CHAIN_PREDICATES, dict) else set(CHAIN_PREDICATES)
        pool = sorted(k for k in bykey if k[1] in chain)
        rng = random.Random(seed)
        org_sites = sorted(set(sites.values()))

        def targeted(buckets, st):
            return missing_slot_sites(buckets)

        def untargeted(buckets, st):
            picks = list(org_sites)
            rng.shuffle(picks)
            return picks

        # the median gold window, so an unplanted key is judged over a comparable stretch of weeks
        wlen = sorted(widx[l["found_to"]] - widx[l["found_from"]] for l in labels["patterns"])
        med = wlen[len(wlen) // 2]
        span_rng = random.Random(10_000 + seed)
        spans = {}

        def other_span(key):
            if key not in spans:
                start = span_rng.randrange(0, max(1, len(weeks) - med))
                spans[key] = (weeks[start], weeks[min(len(weeks) - 1, start + med)])
            return spans[key]

        arms = {}
        for arm, pick in (("targeted", targeted), ("untargeted", untargeted)):
            n = Counter()
            for key in pool:
                lab = goldkey.get(key)
                dec = decoykey.get(key)
                cls = "gold" if lab else ("decoy" if dec else "other")
                # every class is scored over a window, not over the whole world: gold and decoys over their own
                # label window, an unplanted key over a span of the same median length placed by a seeded draw.
                # Scoring one class over all 26 weeks and another over 13 would flatter the unplanted class.
                if lab:
                    span = (lab["found_from"], lab["found_to"])
                elif dec:
                    span = (dec["found_from"], dec["found_to"])
                else:
                    span = other_span(key)
                before, after, asks, claims, rounds, _ = run_key(bykey[key], recs, sites, widx, weeks, span, pick)
                n[f"{cls}_keys"] += 1
                n[f"{cls}_before"] += before
                n[f"{cls}_after"] += after
                if not before:
                    n[f"{cls}_recovered"] += after
                    n[f"{cls}_asks"] += asks
                    n[f"{cls}_claims"] += claims
                    n[f"{cls}_rounds"] += rounds
            arms[arm] = dict(n)
            print(f"seed {seed} {arm}: " + " ".join(f"{k}={v}" for k, v in sorted(n.items())), flush=True)
        results[seed] = arms
    json.dump(results, open(out_path, "w"), indent=1, sort_keys=True)
    print("wrote", out_path)


if __name__ == "__main__":
    out = sys.argv[1]
    ss = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else list(range(6))
    assert all(s in range(6) for s in ss), "dev seeds 0-5 only"
    main(out, ss)

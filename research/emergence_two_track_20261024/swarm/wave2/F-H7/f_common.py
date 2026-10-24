"""F-H7 shared code. Read-only on every repository; writes only under OUT. Dev seeds 0-5.

Three definitions of a record's ROOT, so every H7 number can be recomputed under what a site actually exports:
  event   : the generator's event id (H7's oracle; what h7lib called `events`)
  nd      : root_ref assigned by the SHIPPED ingest code (mycelic.collective.edge.records.RecordStore.ingest) with the
            v4 pack override near_duplicate_roots=(0.65, 14), run on a scratch store under OUT (the real function, not
            a re-implementation); ingest order = record list order = rid order, exactly as run_pipeline / the e2e feed
  default : root_ref assigned by the same code with the shipped pack (near_duplicate_roots=None): origin_ref (always
            None here) -> exact folded-narrative match -> the record itself

Gate model: h7lib (K=3, WIN=6, FRESH 3 weeks, TB_v4 = N1 + N2), imported from the H7 directory so the numbers are
comparable one-for-one with HYPOTHESES.md.
"""
import json, os, sys, shutil, sqlite3
from collections import defaultdict

OUT = os.path.dirname(os.path.abspath(__file__))
H7 = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H7"
R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
OFFICIAL = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/runs/official-ce0e357"
sys.path.insert(0, "/home/user/ng-deploy")
sys.path.insert(0, H7)
import h7lib  # noqa: E402
from h7lib import K, WIN, EDGES, lb, bidx, window_recs, tb_v4, auc  # noqa: E402,F401

from research.e2e_ingest import emergence_world as ew  # noqa: E402
from research.e2e_ingest.emergence_world import received_date, PREDICATES, pack_predicate, CHAIN_PREDICATES  # noqa: E402
from mycelic.collective.edge.weeks import iso_week  # noqa: E402
from mycelic.collective.edge.records import RecordStore  # noqa: E402

ND = (0.65, 14)
CLASSES = ["gold", "d1", "d2", "d3", "d4", "d5", "other"]
KIND_DUP = None  # resolved lazily from corpus module


def classes_of(v):
    if v["class"] == "gold":
        return ["gold"]
    if v["class"] == "decoy":
        return [f"d{c}" for c in v["decoy_classes"]] or ["decoy?"]
    return ["other"]


def roots_by_ingest(records, site_ids, near, tag, seed, size):
    """Run the SHIPPED RecordStore.ingest per site on a scratch store; return {record_ref: root_ref}."""
    work = os.path.join(OUT, "scratch_stores", f"{size}-s{seed}-{tag}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    out = {}
    for sid in site_ids:
        store = RecordStore(os.path.join(work, f"{sid}.sqlite3"), site_id=sid, pack_id="emergence", config_hash="x")
        store.ingest([r for r in records if r["site"] == sid], ingested_at="2024-07-15T00:00:00Z",
                     near_duplicate_roots=near)
        c = sqlite3.connect(store.path)
        out.update(dict(c.execute("SELECT record_ref, root_ref FROM records")))
        c.close()
        store.close()
    shutil.rmtree(work, ignore_errors=True)
    return out


def build(size, seed, *, with_nd=True, keys_from_h7=True):
    """Per-key record tuples [week_idx, site_idx, uid, event, polarity, facet_of_gold, rid, nd_root, default_root]
    plus class/decoy/gold metadata. keys_from_h7: use H7's candidate-pool keys and rule weeks (200 users only);
    otherwise every (entity, chain predicate) key that holds a record, with label windows only."""
    path = os.path.join(OUT, "cache", f"world-{size}-s{seed}{'-h7' if keys_from_h7 else '-all'}.json")
    if os.path.exists(path):
        return json.load(open(path))
    org, corpus, gold = ew.generate(size, seed)
    sites_of_user = ew._user_sites(org)
    site_ids = ew.site_ids(org)
    sidx = {s: i for i, s in enumerate(site_ids)}
    labels = ew.gold_labels(corpus, gold)
    weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS))
    widx = {x: i for i, x in enumerate(weeks)}
    recs = corpus.recs
    ent = {ew.canonical_entity_id(e): i for i, e in enumerate(corpus.entities)}
    records = ew.build_records(corpus, seed=seed)
    ref_of = [r["record_ref"] for r in records]
    nd_root = roots_by_ingest(records, site_ids, ND, "nd", seed, size) if with_nd else {}
    df_root = roots_by_ingest(records, site_ids, None, "default", seed, size)
    # integer ids for roots (stable within a seed)
    nd_id, df_id = {}, {}
    def iid(m, ref):
        if ref not in m:
            m[ref] = len(m) + 10_000_000
        return m[ref]
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"])
        pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None:
            continue
        for e in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if e >= 0:
                bykey[(e, pp)].append(r)
    facet_of_key = defaultdict(set)
    gold_meta = {}
    for lab in labels["patterns"]:
        p = corpus.patterns[lab["pattern"]]
        facet_of_key[lab["key"]].update(int(r) for fr in p.facet_records for r in fr)
        gold_meta[lab["key"]] = {"pattern": lab["id"], "rare": lab["rare"], "contradicted": lab["contradicted"],
                                 "window": [lab["found_from"], lab["found_to"]], "label_sites": lab["sites"]}
    decoy_classes = defaultdict(set)
    decoy_windows = defaultdict(list)
    for d in labels["decoys"]:
        for k in d["keys"]:
            decoy_classes[k].add(int(d["class"]))
            decoy_windows[k].append([d["found_from"], d["found_to"]])
    kind_dup = int(__import__("research.mycelic.corpus", fromlist=["KIND_DUP"]).KIND_DUP)

    def tuples(rows, fac):
        out = []
        for r in rows:
            rid = int(r)
            out.append([widx[iso_week(received_date(int(recs[r]["t"])))], sidx[sites_of_user[int(recs[r]["uid"])]],
                        int(recs[r]["uid"]), int(recs[r]["event"]), int(recs[r]["polarity"]), int(rid in fac), rid,
                        iid(nd_id, nd_root[ref_of[rid]]) if with_nd else -1, iid(df_id, df_root[ref_of[rid]]),
                        int(recs[r]["kind"] == kind_dup)])
        return out

    out = {"seed": seed, "size": size, "sites": site_ids, "weeks": weeks, "keys": {}, "n_records": len(recs)}
    if keys_from_h7:
        H = json.load(open(f"{H7}/cache/seed-{seed}.json"))
        assert H["sites"] == site_ids and H["weeks"] == weeks
        for key, v in H["keys"].items():
            _, eid, pred = key.split(":")
            rows = bykey[(ent[eid], pred)]
            fac = facet_of_key.get(key, set())
            t = tuples(rows, fac)
            # one-for-one check against H7's cache (first six fields)
            assert [x[:6] for x in t] == v["recs"], key
            out["keys"][key] = {**{k2: v[k2] for k2 in ("class", "detector_state", "rule_weeks", "candidate_weeks",
                                                        "decoy_classes", "decoy_windows", "gold")}, "recs": t}
    else:
        for (e, pp), rows in bykey.items():
            key = f"entity:{ew.canonical_entity_id(corpus.entities[e])}:{pp}"
            fac = facet_of_key.get(key, set())
            cls = "gold" if key in gold_meta else ("decoy" if key in decoy_classes else "other")
            out["keys"][key] = {"class": cls, "detector_state": None, "rule_weeks": [], "candidate_weeks": [],
                                "decoy_classes": sorted(decoy_classes.get(key, ())),
                                "decoy_windows": decoy_windows.get(key, []), "gold": gold_meta.get(key),
                                "recs": tuples(rows, fac)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w"))
    return out


# ------------------------------------------------------------------ gate model with a chosen root field
ROOT_FIELD = {"event": 3, "nd": 7, "default": 8}


def site_stats(wrecs, root_field="event", alpha="blind"):
    """h7lib.site_stats with the root taken from a chosen field (3 event, 7 nd root, 8 default root)."""
    rf = ROOT_FIELD[root_field]
    acc = defaultdict(lambda: {"pos": 0, "neg": 0, "users_all": set(), "events_all": set(), "users_pos": set(),
                               "events_pos": set(), "newest_all": -1, "newest_pos": -1})
    for row in wrecs:
        wi, site, uid, pol = row[0], row[1], row[2], row[4]
        ev = row[rf]
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
        else:
            rec, users, events, newest = a["pos"], len(a["users_pos"]), len(a["events_pos"]), a["newest_pos"]
        if rec <= 0:
            continue
        out[s] = {"records": rec, "users": users, "events": events, "newest": newest, "pos": a["pos"], "neg": a["neg"]}
    return out


def veto(st, T=1, field="records"):
    """H7-1: drop k-sites whose exported support (or reporters) bucket index exceeds their roots bucket index by >= T."""
    if T is None:
        return st
    return {s: v for s, v in st.items() if not (v["records"] >= K and bidx(v[field]) - bidx(v["events"]) >= T)}


def passes(recs, W, root_field, T, alpha="blind", field="records"):
    """TB_v4 passes at some week of W (week indices) under the root definition, with/without the veto."""
    for w in W:
        wr = window_recs(recs, w - WIN + 1, w)
        if tb_v4(veto(site_stats(wr, root_field, alpha), T, field), w)[0]:
            return True
    return False


def echo_score(recs, W, root_field, field="records"):
    best = -1
    for w in W:
        for s, x in site_stats(window_recs(recs, w - WIN + 1, w), root_field).items():
            if x["records"] >= K:
                best = max(best, bidx(x[field]) - bidx(x["events"]))
    return best


def label_window(D, v):
    widx = {w: i for i, w in enumerate(D["weeks"])}
    g = v["gold"]
    lo = widx[g["window"][0]]
    hi = widx[g["window"][1]] if g["window"][1] in widx else len(D["weeks"]) - 1
    return list(range(lo, hi + 1))


def decoy_window_weeks(D, v):
    widx = {w: i for i, w in enumerate(D["weeks"])}
    W = set()
    for a, b in v["decoy_windows"]:
        lo = widx[a] if a in widx else 0
        hi = widx[b] if b in widx else len(D["weeks"]) - 1
        W.update(range(lo, hi + 1))
    return sorted(W)

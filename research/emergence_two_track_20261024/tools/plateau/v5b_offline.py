"""Offline estimate for v5b, "ask when the evidence is ready" (no e2e run, no model call, dev seeds only).

HQ-side readiness uses only what HQ already holds: each site's k-suppressed weekly cells for the key (count, roots,
reporters; '<k' read as its lower bound 1), visible at the week's as_of. No label, no raw record.
Outcome model: the researcher's calibrated evidence check (model B, TB_v4 gate; 98.6% agreement with the system at
v4's asked weeks), from tools/plateau/ceiling_b.py. Scoring: the official candidate_label() and the harness's decoy
rule (facet-0 key, snapshot week inside the decoy window). Estimates, not results."""
import json, shutil, sys, tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from ceiling_b import site_stats, gates, WIN  # noqa: E402
from research.e2e_ingest import emergence_world as ew  # noqa: E402
from research.e2e_ingest.emergence_world import PREDICATES, pack_predicate, CHAIN_PREDICATES  # noqa: E402
from research.routing_spike.world import candidate_label  # noqa: E402
from mycelic.collective.detect.detectors import detect  # noqa: E402
from mycelic.collective.detect.rules import series_key  # noqa: E402
from mycelic.collective.edge.weeks import week_sunday  # noqa: E402
from mycelic.collective.detect.detectors import RUN_CHANNELS  # noqa: E402

K, FRESH = 3, 42
ew.check_world_counts = lambda *a: None
pack = Path(tempfile.mkdtemp()) / "emergence"
shutil.copytree(ew.PACK_DIR, pack)
f = pack / "detectors.json"; d = json.loads(f.read_text()); d.update(min_history_weeks=1, rule_snapshots=True)
f.write_text(json.dumps(d))
q = pack / "questions.json"; qd = json.loads(q.read_text())
qd.setdefault("pushdown", {}).update(weak_corroborating_sites=1, roots_from_k_sites_only=True); q.write_text(json.dumps(qd))
e = pack / "egress.json"; ed = json.loads(e.read_text()); ed["near_duplicate_roots"] = {"jaccard": 0.65, "days": 14}
e.write_text(json.dumps(ed))

def lb(x):
    return 1 if x is None else x

def hq_ready(store, chans, key, weeks, w, as_of):
    """TB_v4-shaped gate on HQ's own k-suppressed cells, lower bounds only."""
    et, eid, pred = key.split(":")
    cells = store.key_cells(et, eid, pred, weeks[w - WIN + 1], weeks[w], as_of, chans)
    per = defaultdict(lambda: {"n": 0, "roots": 0, "rep": 0, "newest": None})
    for c in cells:
        s = per[c.site]; s["n"] += lb(c.n); s["roots"] += lb(c.n_roots); s["rep"] += lb(c.n_reporters)
        s["newest"] = max(s["newest"] or c.iso_week, c.iso_week)
    asof_d = date.fromisoformat(as_of)
    def fresh(sel):
        newest = max(v["newest"] for v in sel)
        ws = week_sunday(newest); ws = date.fromisoformat(ws) if isinstance(ws, str) else ws
        return (asof_d - ws).days <= FRESH
    ks = sorted((v for v in per.values() if v["n"] >= K), key=lambda v: (-v["roots"], -v["rep"]))
    weak = [v for v in per.values() if 0 < v["n"] < K]
    def full(sel):
        return (sel and sum(v["roots"] for v in sel if v["roots"] >= K) >= 3 and sum(v["rep"] for v in sel) >= 3
                and fresh(sel))
    return bool((len(ks) >= 2 and full(ks[:3])) or (len(ks) >= 1 and weak and full(ks[:1] + weak[:1])))

tot = defaultdict(Counter); agree = Counter()
for seed in [int(x) for x in sys.argv[1].split(",")]:
    world = ew.build_emergence_world(200, seed, tempfile.mkdtemp(), pack_dir=pack, top_n=None)
    store, chans = world.pipeline.store, RUN_CHANNELS[ew.RUN_CHANNEL]
    full_run = detect(store, as_of=world.as_of, run_channel=ew.RUN_CHANNEL, tie_salt=world.tie_salt)
    weeks = [x["week"] for x in full_run["weeks"]]; widx = {x: i for i, x in enumerate(weeks)}
    as_of = {x["week"]: x["as_of"] for x in full_run["weeks"]}
    # outcome model (raw records; offline only)
    corpus = world.corpus
    sites = ew._user_sites(ew.generate(200, seed)[0]); recs = corpus.recs
    allweeks = list(ew.world_weeks(ew.START, ew.N_WEEKS)); aidx = {x: i for i, x in enumerate(allweeks)}
    ent = {ew.canonical_entity_id(en): i for i, en in enumerate(corpus.entities)}
    bykey = defaultdict(list)
    for r in range(len(recs)):
        pr = int(recs[r]["pred"]); pp = pack_predicate(PREDICATES[pr]) if pr >= 0 else None
        if pp is None: continue
        for en in {int(recs[r]["anchor"]), int(recs[r]["aux"])}:
            if en >= 0: bykey[(en, pp)].append(r)
    cache = {}
    def ok(key, wk):
        if (key, wk) not in cache:
            _, eid, pred = key.split(":"); w = aidx[wk]
            cache[(key, wk)] = gates(site_stats(bykey[(ent[eid], pred)], recs, sites, aidx, w - WIN + 1, w, True),
                                     w, allweeks, False)["TB_v4"]
        return cache[(key, wk)]
    # decoy keys as the harness builds them
    decoys = []
    for dec in world.labels["decoys"]:
        p = corpus.patterns[int(dec["pattern"])]
        first = int(p.facet_records[0][0]) if p.facet_records and p.facet_records[0] else None
        if first is None: continue
        anchor = ew.canonical_entity_id(corpus.entities[int(corpus.recs[first]["anchor"])])
        decoys.append((series_key(ew.ENTITY_TYPE, anchor, CHAIN_PREDICATES[int(p.chain)]), dec["found_from"],
                       dec["found_to"], str(dec["class"]), int(dec["pattern"])))
    rare = {p["id"] for p in world.labels["patterns"] if p["rare"]}
    # per-candidate policies
    pol = {"P0 once, first week (v4)": {}, "P2 timer, every 2nd week (v5)": {}, "B1 once, when HQ-ready": {},
           "B2 first week + when HQ-ready": {}, "B3 first week + every HQ-ready week": {},
           "B4 first week + every 2nd HQ-ready week": {}}
    for c in world.candidates:
        key, first = c["key"], c["snapshot"]["week"]
        rule = c.get("rule") or {}
        later = sorted(w for w in set(c.get("candidate_weeks") or []) | set(rule.get("weeks") or []) | {first}
                       if w >= first and w in widx and widx[w] >= WIN - 1)
        ready = [w for w in later if hq_ready(store, chans, key, weeks, widx[w], as_of[w])]
        pol["P0 once, first week (v4)"][key] = [first]
        pol["P2 timer, every 2nd week (v5)"][key] = later[::2]
        pol["B1 once, when HQ-ready"][key] = [ready[0]] if ready else [first]
        pol["B2 first week + when HQ-ready"][key] = sorted({first, ready[0]}) if ready else [first]
        pol["B3 first week + every HQ-ready week"][key] = sorted({first, *ready})
        pol["B4 first week + every 2nd HQ-ready week"][key] = sorted({first, *ready[::2]})
        for w in later:                          # predictor calibration over every presence week
            agree[(hq_ready(store, chans, key, weeks, widx[w], as_of[w]), ok(key, w))] += 1
    for name, asks in pol.items():
        found, acc, bg, nq = set(), set(), 0, 0
        for key, wks in asks.items():
            for wk in wks:
                nq += 1
                if not ok(key, wk): continue
                lab = candidate_label(key, wk, world.labels)
                if lab["label"] == "true": found.add(lab["pattern"])
                hit = [(cls, pid) for k0, a, b, cls, pid in decoys if k0 == key and a <= wk <= b]
                acc.update(hit)
                if lab["label"] == "background" and not hit: bg += 1
        t = tot[name]; t["found"] += len(found); t["rare"] += len(found & rare); t["q"] += nq; t["bg"] += bg
        t["dec"] += len(acc)
        for cls, _ in acc: t[f"d{cls}"] += 1
    tot["_n"]["gold"] += len(world.labels["patterns"]); tot["_n"]["rare"] += len(rare)
    print(f"seed {seed} done", flush=True)
    world.pipeline.close()
n = tot.pop("_n")
print(f"\nHQ-ready vs evidence check, over every presence week: {dict(agree)}")
tp, fp, fn = agree[(True, True)], agree[(True, False)], agree[(False, True)]
print(f"  precision {tp/(tp+fp):.3f}  recall {tp/(tp+fn):.3f}")
for name, c in tot.items():
    print(f"{name:32s} found {c['found']}/{n['gold']}={c['found']/n['gold']:.3f}  rare {c['rare']}/{n['rare']}  "
          f"decoys {c['dec']} (1:{c['d1']} 2:{c['d2']} 3:{c['d3']} 4:{c['d4']} 5:{c['d5']})  bg-supported {c['bg']}  "
          f"questions {c['q']}")

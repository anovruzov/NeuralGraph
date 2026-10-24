"""KILL 2a: offline gate replay of the RECORDED verdicts of the six official ce0e357 reports (shipped pack, R0 gate),
shipped `evaluate` vs the H7-1 veto (`gate_veto.patch` applied to a copy of gate.py under OUT; the repository is not
touched). Recounts found gold and accepted decoys by type per seed against the recorded 93 / 118.
Not an e2e run: HQ's pd_questions / pd_routes / pd_verdicts / pd_conclusions are read from copies of the stores."""
import json, sqlite3, sys, types, importlib
from collections import Counter, defaultdict
from dataclasses import replace
from f_common import *  # noqa
from research.routing_spike.world import candidate_label
from mycelic.collective.packs.loader import load_pack_dir
from mycelic.collective.pushdown import gate as shipped_gate
from mycelic.collective.pushdown.gate import VerdictRecord

# ---- build the patched gate module from the patched copy (exec with the package context so relative imports work)
src = open(f"{OUT}/patched/new/mycelic/collective/pushdown/gate.py").read()
veto_gate = types.ModuleType("mycelic.collective.pushdown.gate_veto")
veto_gate.__package__ = "mycelic.collective.pushdown"
veto_gate.__file__ = f"{OUT}/patched/new/mycelic/collective/pushdown/gate.py"
sys.modules[veto_gate.__name__] = veto_gate
exec(compile(src, veto_gate.__file__, "exec"), veto_gate.__dict__)
_orig_from_pack = veto_gate.GateParams.from_pack.__func__
GAP = int(sys.argv[1]) if len(sys.argv) > 1 else 1


def _from_pack_with_gap(cls, pack):
    pd = pack.pushdown
    return cls(k=pack.egress.k, labels=veto_gate.verdict_buckets(pack), min_confirming_sites=pd.min_confirming_sites,
               min_independent_roots=pd.min_independent_roots, min_independent_reporters=pd.min_independent_reporters,
               freshness_days=pd.freshness_days, close_lag_days=pack.egress.close_lag_days,
               weak_corroborating_sites=pd.weak_corroborating_sites, roots_from_k_sites_only=pd.roots_from_k_sites_only,
               echo_veto_bucket_gap=GAP)


veto_gate.GateParams.from_pack = classmethod(_from_pack_with_gap)   # the unpatched loader has no such pack key

pack = load_pack_dir(ew.PACK_DIR)
print(f"pack config_hash {pack.config_hash[:12]}  near_duplicate_roots={pack.egress.near_duplicate_roots}  "
      f"N1={pack.pushdown.weak_corroborating_sites} N2={pack.pushdown.roots_from_k_sites_only}  veto gap={GAP}\n")

res = {}
print("| seed | questions | verdicts | confirms | confirms with support bucket > roots bucket | replay == recorded status | "
      "found (recorded) | found (shipped replay) | found (veto) | decoys accepted (recorded) | by type | decoys (veto) | by type |")
print("|" + "---|" * 13)
TF = Counter()
for seed in range(6):
    rep = json.load(open(f"{OFFICIAL}/seed-{seed}/e2e-ingest-report.json"))
    disc = rep["discovery"]
    con = sqlite3.connect(f"file:{OUT}/db/seed-{seed}/collective.sqlite3?mode=ro", uri=True)
    assert dict(con.execute("select key,value from store_info"))["config_hash"] == pack.config_hash
    org, corpus, gold = ew.generate(200, seed)
    labels = ew.gold_labels(corpus, gold)
    snap = {c["question_id"]: c for c in disc["fabric_conclusions"]}
    questions = {qid: (json.loads(body), ck) for qid, body, ck in con.execute("select question_id, body, candidate_key from pd_questions")}
    routes = defaultdict(dict)
    for qid, site, role in con.execute("select question_id, site, role from pd_routes"):
        routes[qid][site] = role
    recs = defaultdict(list)
    n_conf = n_gap = 0
    for qid, site, seq, source, body, ras in con.execute("select question_id, site, seq, source, body, received_as_of from pd_verdicts"):
        b = json.loads(body)
        recs[qid].append(VerdictRecord(site, seq, source, b, ras))
        if source == "site" and b["verdict"] == "confirm":
            n_conf += 1
            labs = veto_gate.verdict_buckets(pack)
            if labs.index(b["support_bucket"]) - labs.index(b["roots_bucket"]) >= GAP:
                n_gap += 1
    concl = {}
    for qid, as_of, status, version in con.execute("select question_id, as_of, status, version from pd_conclusions"):
        if qid not in concl or version > concl[qid][2]:
            concl[qid] = (as_of, status, version)
    # the harness's own rules (research/e2e_ingest/harness.py:1291-1324): found = gold patterns with a supported,
    # lineage-ok conclusion on their key inside their window; a decoy is accepted when such a conclusion sits on its
    # (facet-0 anchor, chain) key inside its window; decoys are counted as distinct patterns per class
    from mycelic.collective.detect.rules import series_key
    decoy_keys = {}
    classes = {}
    for d in labels["decoys"]:
        pid = int(d["pattern"]); pp = corpus.patterns[pid]
        first = int(pp.facet_records[0][0]) if pp.facet_records and pp.facet_records[0] else None
        if first is None: continue
        anchor = ew.canonical_entity_id(corpus.entities[int(corpus.recs[first]["anchor"])])
        decoy_keys[pid] = (series_key(ew.ENTITY_TYPE, anchor, ew.CHAIN_PREDICATES[int(pp.chain)]), d["found_from"], d["found_to"])
        classes[pid] = int(d["class"])

    def score(status_of):
        found, acc = set(), {}
        for qid, (q, ck) in questions.items():
            c = snap.get(qid)
            if c is None or not c.get("lineage_ok") or status_of[qid] != "supported": continue
            key = series_key(ew.ENTITY_TYPE, str(c["entity_id"]), str(c["predicate"])); week = str(c["snapshot_week"])
            lab = candidate_label(key, week, labels)
            if lab["label"] == "true": found.add(lab["pattern"])
            for pid, (key0, a, b) in decoy_keys.items():
                if key == key0 and a <= week <= b: acc.setdefault(classes[pid], set()).add(pid)
        return found, {t: len(v) for t, v in sorted(acc.items())}

    same = 0; flips = []
    st_rec, st_ship, st_veto = {}, {}, {}
    for qid, (q, ck) in questions.items():
        as_of, rec_status, _ = concl[qid]
        r_ship = shipped_gate.evaluate(pack, q, routes[qid], recs[qid], as_of=as_of)
        r_veto = veto_gate.evaluate(pack, q, routes[qid], recs[qid], as_of=as_of)
        st_rec[qid] = rec_status; st_ship[qid] = r_ship.status; st_veto[qid] = r_veto.status
        same += r_ship.status == rec_status
        if r_veto.status != r_ship.status or r_veto.to_dict()["support"].get("echo_vetoed_sites"):
            c = snap.get(qid, {})
            flips.append((qid[:10], ck, c.get("snapshot_week"), r_ship.status, r_veto.status,
                          r_veto.to_dict()["support"].get("echo_vetoed_sites")))
    found_rec, dec_rec = score(st_rec); found_ship, dec_ship = score(st_ship); found_veto, dec_veto = score(st_veto)
    rec_found = set(disc["found_patterns"]); rep_dec = {int(k): v for k, v in disc["decoy_accepted_by_type"].items()}
    ok = found_rec == rec_found and dec_rec == rep_dec
    print(f"| {seed} | {len(questions)} | {sum(len(v) for v in recs.values())} | {n_conf} | {n_gap} | {same}/{len(questions)} | "
          f"{len(rec_found)} | {len(found_ship)} | {len(found_veto)} | {sum(rep_dec.values())} | {rep_dec} | "
          f"{sum(dec_veto.values())} | {dec_veto} |" + ("" if ok else f"  <-- recount does not reproduce the report: {found_rec} {dec_rec}"))
    for f in flips: print(f"    vetoed/flipped: {f}")
    res[seed] = {"questions": len(questions), "confirms": n_conf, "confirms_with_gap": n_gap, "status_match": same,
                 "found_recorded": len(rec_found), "found_shipped_replay": len(found_ship), "found_veto": len(found_veto),
                 "decoys_recorded": rep_dec, "decoys_shipped_replay": dec_ship, "decoys_veto": dec_veto,
                 "recount_reproduces_report": ok, "questions_with_a_vetoed_site_or_flip": flips}
    TF["found_rec"] += len(rec_found); TF["found_veto"] += len(found_veto); TF["dec_rec"] += sum(rep_dec.values())
    TF["dec_veto"] += sum(dec_veto.values()); TF["gap"] += n_gap; TF["conf"] += n_conf; TF["flips"] += sum(1 for f in flips if f[3] != f[4])
    for k2, v2 in rep_dec.items(): TF[f"dec_rec_t{k2}"] += v2
    for k2, v2 in dec_veto.items(): TF[f"dec_veto_t{k2}"] += v2
print(f"\nPooled six seeds: found {TF['found_rec']} -> {TF['found_veto']} (delta {TF['found_veto']-TF['found_rec']}); "
      f"decoys {TF['dec_rec']} -> {TF['dec_veto']} (delta {TF['dec_veto']-TF['dec_rec']}); "
      f"by type recorded " + ", ".join(f"t{t}={TF[f'dec_rec_t{t}']}" for t in (1,2,3,4,5)) + " ; veto "
      + ", ".join(f"t{t}={TF[f'dec_veto_t{t}']}" for t in (1,2,3,4,5)))
print(f"Confirms whose support bucket exceeds their roots bucket by >= {GAP}: {TF['gap']} of {TF['conf']} recorded confirms; "
      f"questions whose status the veto changes: {TF['flips']}.")
json.dump(res, open(f"{OUT}/results_2_replay_official.json", "w"), indent=1, default=str)

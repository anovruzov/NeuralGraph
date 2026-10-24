"""Check the near-duplicate root rule through the REAL site ingest (EdgeSite.ingest -> RecordStore.ingest), against the
generator's ground truth (evaluation only): echo (site, event) groups of decoy class 4 should collapse to one root;
true-pattern facet records should keep their own root (false merges). Also reports ingest time with the rule on/off."""
import collections, json, shutil, sqlite3, sys, tempfile, time
from pathlib import Path
from research.e2e_ingest import emergence_world as ew
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.packs.loader import load_pack
J, D = float(sys.argv[1]), int(sys.argv[2])

def pack_with(near):
    d = Path(tempfile.mkdtemp()) / "emergence"; shutil.copytree(ew.PACK_DIR, d)
    if near:
        f = d / "egress.json"; e = json.loads(f.read_text()); e["near_duplicate_roots"] = {"jaccard": J, "days": D}
        f.write_text(json.dumps(e, sort_keys=True, separators=(",", ":")))
    return load_pack(str(d))

for seed in map(int, sys.argv[3].split(",")):
    org, corpus, gold = ew.generate(200, seed)
    recs = ew.build_records(corpus, seed=seed); master = ew.master_data(corpus)
    out = {}
    for near in (False, True):
        pack = pack_with(near); work = Path(tempfile.mkdtemp()); t0 = time.perf_counter(); root_of = {}
        for sid in ew.site_ids(org):
            site = EdgeSite(pack, sid, work / "edge", runtime=None, clock=lambda: "2024-07-01T00:00:00Z",
                            master_data=master[sid], hq_dir=work / "hq")
            site.ingest([r for r in recs if r["site"] == sid])
            c = sqlite3.connect(str(site.store.path))
            root_of.update(dict(c.execute("SELECT record_ref, root_ref FROM records")))
            c.close(); site.close()
        out[near] = (root_of, time.perf_counter() - t0)
    root_of, secs = out[True]
    refs = [r["record_ref"] for r in recs]
    size = collections.Counter(root_of.values())
    true_rids = {int(x) for pid in gold.discoverable for fr in corpus.patterns[pid].facet_records for x in fr}
    false_merge = sum(1 for i in true_rids if root_of[refs[i]] != out[False][0][refs[i]] or size[root_of[refs[i]]] > size.get(out[False][0][refs[i]], 1) and root_of[refs[i]] != refs[i])
    echo = [int(x) for p in gold.decoys if corpus.patterns[p].decoy_type == 4 for fr in corpus.patterns[p].facet_records for x in fr]
    groups = collections.defaultdict(set)
    for i in echo:
        groups[(recs[i]["site"], int(corpus.recs[i]["event"]))].add(root_of[refs[i]])
    per = [len(v) for v in groups.values()]
    print(f"seed {seed} J={J} D={D}: true-pattern records {len(true_rids)}, root changed by the rule {false_merge}; echo groups "
          f"{len(per)}, roots/group {sum(per)/len(per):.2f}; ingest {out[False][1]:.1f}s off / {secs:.1f}s on", flush=True)

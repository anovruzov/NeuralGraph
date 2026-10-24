"""Offline check of the near-duplicate root rule against the generator's ground truth (evaluation only): how many
echo groups (decoy class 4, one event) collapse, and how many TRUE-pattern facet records get merged with any other
record (false merges). Uses the shipped folded() and the rule's own function on an in-memory per-site table."""
import sqlite3, sys, collections
from research.e2e_ingest import emergence_world as ew
from mycelic.collective.edge.records import _near_duplicate_root
from mycelic.collective.packs.canonical import folded
from mycelic.collective.jsonio import canonical_dumps
J, D = float(sys.argv[1]), int(sys.argv[2])
for seed in map(int, sys.argv[3].split(",")):
    org, corpus, gold = ew.generate(200, seed)
    recs = ew.build_records(corpus, seed=seed)
    conns = {}
    root_of = {}
    for rid, r in enumerate(recs):
        c = conns.get(r["site"])
        if c is None:
            c = conns[r["site"]] = sqlite3.connect(":memory:")
            c.execute("CREATE TABLE records (seq INTEGER, received_date TEXT, narrative TEXT, structured TEXT, root_ref TEXT, nkey TEXT)")
        text = folded(r["narrative"])
        ex = c.execute("SELECT root_ref FROM records WHERE nkey=? ORDER BY seq LIMIT 1", (text,)).fetchone()
        root = ex[0] if ex else None
        if root is None:
            root = _near_duplicate_root(c, r, text, J, D)
        root = root or r["record_ref"]
        root_of[rid] = root
        c.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (rid, r["received_date"], r["narrative"], canonical_dumps(r["entities"]), root, text))
    merged = collections.Counter(root_of.values())
    true_rids = {int(x) for pid in gold.discoverable for fr in corpus.patterns[pid].facet_records for x in fr}
    false_merge = sum(1 for rid in true_rids if root_of[rid] != recs[rid]["record_ref"] or merged[root_of[rid]] > 1)
    echo = [p for p in gold.decoys if corpus.patterns[p].decoy_type == 4]
    echo_rids = [int(x) for p in echo for fr in corpus.patterns[p].facet_records for x in fr]
    by_site_event = collections.defaultdict(set)
    for rid in echo_rids:
        by_site_event[(recs[rid]["site"], int(corpus.recs[rid]["event"]))].add(root_of[rid])
    roots_per_group = [len(v) for v in by_site_event.values()]
    print(f"seed {seed} J={J} D={D}: true-pattern records {len(true_rids)}, merged with another record {false_merge}; "
          f"echo (site,event) groups {len(roots_per_group)}, mean roots per group {sum(roots_per_group)/len(roots_per_group):.2f} "
          f"(1.00 = fully collapsed), echo records {len(echo_rids)}", flush=True)

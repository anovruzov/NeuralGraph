"""F-H5 KILL 6 (hand-off). Top-20 keys per dev seed under THE pre-registered ranker (1[sync<=1.25], n_kcells in window,
longest_run) on H5's REAL Boundary cells (cells-real-seed-N.json), deterministic tie-break sha256('e2e-ingest-v1'|key)
(emergence_world.ordered_candidates' rule). Columns: seed, rank, key, score (gate|kcells|lr|sync). No label is written;
no verdict is claimed. The replay turns these into verdicts."""
import sys, json, csv
sys.path.insert(0, "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/F-H5")
from f_common import *
rows = load_pool()
real = {s: json.load(open(f"{H5}/cells-real-seed-{s}.json"))["cells"] for s in SEEDS}
F = {(r["seed"], r["key"]): h5_feats(site_weeks(real[r["seed"]].get(r["key"], {}), True), r["rule_weeks"]) for r in rows}
score = lambda r: gated(F[(r["seed"], r["key"])])
with open(f"{OUT}/top20_per_seed.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["seed", "rank", "key", "score"])
    for s in SEEDS:
        sub = [r for r in rows if r["seed"] == s]
        for i, r in enumerate(topn_det(sub, score, 20), 1):
            f = F[(s, r["key"])]
            w.writerow([s, i, r["key"], f"gate={int(f['sync'] <= THETA)}|kcells={f['kcells']}|lr={f['lr']}|sync={f['sync']:.3f}|win={f['lo']}-{f['hi']}|w_maxk={f['w_maxk']}"])
print("wrote", f"{OUT}/top20_per_seed.csv")
# scoring-side tally for the report only (labels not written to the CSV)
for s in SEEDS:
    sub = [r for r in rows if r["seed"] == s]; top = topn_det(sub, score, 20)
    print(f"  seed {s}: gold {sum(r['label']=='gold' for r in top)} decoy {sum(r['label']=='decoy' for r in top)} other {sum(r['label']=='other' for r in top)} | gold F/TB_v4 any-week-feasible {sum(r['label']=='gold' and r['feasible'] for r in top)}")

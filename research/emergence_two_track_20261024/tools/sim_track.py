"""Simulator track (SPEC section 4: 10,000 and 50,000 users are simulated: true; never end-to-end).
Reproduces H_mycelic_full's `found_anywhere_in_register` at a scale over seeds, writing rows outside the repo."""
import json, sys, time
from research.mycelic.evalm import evaluate
from research.mycelic.models import allocation
from research.mycelic.runner import build_world, run_arch
scale, seeds, out = int(sys.argv[1]), [int(x) for x in sys.argv[2].split(",")], sys.argv[3]
with open(out, "a") as fh:
    for seed in seeds:
        t0 = time.time(); w = build_world(scale, seed); tb = time.time() - t0
        t1 = time.time(); res = run_arch("H_mycelic_full", w, allocation("back-loaded"), seed)
        met = evaluate(w.corpus, w.gold, res)
        row = {"simulated": True, "arch": "H_mycelic_full", "scale": scale, "seed": seed, "build_s": round(tb, 1),
               "run_s": round(time.time() - t1, 1), "n_users": len(w.org.user_ids) if hasattr(w, "org") else None,
               "n_records": len(w.corpus.recs), **{k: v for k, v in met.items() if isinstance(v, (int, float, str, bool))}}
        fh.write(json.dumps(row) + "\n"); fh.flush()
        print(f"scale {scale} seed {seed} found={met['found_anywhere_in_register']:.4f} rare={met.get('rare_signal_recall')} "
              f"users={row['n_users']} records={row['n_records']} build={tb:.0f}s run={row['run_s']}s", flush=True)

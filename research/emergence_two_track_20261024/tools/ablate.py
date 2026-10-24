"""DIAGNOSTIC ABLATION wrapper (development seeds only; never an official baseline, never a frozen candidate).
Runs the unmodified `run` job with (a) check_world_counts bypassed so seeds 1-5 can run, and (b) optionally a copy of
the frozen pack whose detector settings are overridden. Usage:
    ablate.py --pack-override '{"detectors": {"min_history_weeks": 0}}' --tag h0 -- run --size 200 --seed 0 ...
The override and the resulting pack config hash are printed and written beside the report."""
import argparse, functools, json, shutil, sys, tempfile
from pathlib import Path
from research.e2e_ingest import emergence_world as ew

ap = argparse.ArgumentParser()
ap.add_argument("--pack-override", default=None)
ap.add_argument("--tag", required=True)
ap.add_argument("--m", type=int, default=None, help="sites HQ routes each question to (shipped: 3); 0 = every site")
ap.add_argument("--candidates", default="snapshot", choices=["snapshot", "persist", "persist+rule"],
                help="snapshot: shipped (one question per key at its first snapshot); persist: also re-ask while the "
                     "candidate persists, one question per later candidate week; persist+rule: also ask "
                     "rule-only candidates the same way")
ap.add_argument("--order", default="shipped", choices=["shipped", "rulefirst"],
                help="shipped: score desc (M21); rulefirst: detector candidates whose rule channel fired on or before "
                     "their snapshot week first, then score desc; rule-only candidates last (inference-time fields only)")
ap.add_argument("--extra-size", type=int, default=None,
                help="DIAGNOSTIC scale probe: add a section 3.1 row for this target_users (seed-invariant counts as the "
                     "benchmark defines them; drawn counts 0, so the run's table_row_diff reports every drawn count)")
a, rest = ap.parse_known_args()
rest = [x for x in rest if x != "--"]
ew.check_world_counts = lambda counts, target_users: None
if a.extra_size is not None:
    _inv = next(iter(ew.WORLD_TABLE.values()))
    ew.WORLD_TABLE = {**ew.WORLD_TABLE, a.extra_size: {
        "users": 0, "sites": 0, "teams": 0, "records": 0, "patterns_real": _inv["patterns_real"],
        "gold_discoverable": _inv["gold_discoverable"], "gold_rare": 0, "decoys": _inv["decoys"],
        "entities": _inv["entities"]}}
    print(f"ABLATION {a.tag}: DIAGNOSTIC scale probe, target_users {a.extra_size} (not a section 3.1 size)", flush=True)
if a.m is not None:
    ew.M = a.m if a.m > 0 else 1000
    print(f"ABLATION {a.tag}: M = {ew.M}", flush=True)
if a.pack_override:
    over = json.loads(a.pack_override)
    pack = Path(tempfile.mkdtemp(prefix=f"pack-{a.tag}-")) / "emergence"
    if a.extra_size is not None:
        # the frozen pack registers the <=2,000-user worlds' 150 entities and sites; a larger world has more of both,
        # so the pack is built from this world's own corpus by the official builder (emergence_pack.write_pack)
        from research.e2e_ingest import emergence_pack
        _seed = int(rest[rest.index("--seed") + 1])
        _corpus = ew.generate(a.extra_size, _seed)[1]
        emergence_pack.write_pack(pack, corpus=_corpus)
        from mycelic.collective.packs import loader as _loader
        _n = len(_corpus.entities)
        if _n > 500:   # the loader's schema caps alias-only entity ids at 500 (loader.py S_ET); raised IN MEMORY only
            _loader.S_ET = _loader._schema(_loader._closed({
                "label": _loader._LABEL80, "separator": {"type": ["string", "null"], "enum": ["-", "/", None]},
                "case": {"type": "string", "enum": list(_loader.CASES)}, "strip_leading_zeros": _loader._BOOL,
                "exact_match_metric": _loader._BOOL, "egress": _loader._BOOL,
                "ids": {"type": ["array", "null"], "items": _loader._string(1, 40, pattern=_loader.ALIAS_ONLY_ID_RE.pattern),
                        "minItems": 1, "maxItems": _n}}))
            print(f"ABLATION {a.tag}: DIAGNOSTIC loader entity-id cap raised in memory 500 -> {_n}", flush=True)
        print(f"ABLATION {a.tag}: pack built from the {a.extra_size}-user seed-{_seed} world by emergence_pack.write_pack",
              flush=True)
    else:
        shutil.copytree(ew.PACK_DIR, pack)
    for fname, fields in over.items():
        f = pack / f"{fname}.json"; d = json.loads(f.read_text())

        def merge(dst, src):
            for k, v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    merge(dst[k], v)
                else:
                    dst[k] = v
        merge(d, fields)
        f.write_text(json.dumps(d, sort_keys=True, separators=(",", ":")))
    ew.build_emergence_world = functools.partial(ew.build_emergence_world, pack_dir=pack)
    print(f"ABLATION {a.tag}: pack override {over} at {pack}", flush=True)
else:
    print(f"DIAGNOSTIC {a.tag}: guard bypass only, frozen pack", flush=True)
if a.order == "rulefirst":
    from mycelic.collective.jsonio import sha256_hex

    def rulefirst(result, *, tie_salt=ew.TIE_SALT, top_n=ew.TOP_N):
        found = [c for c in result["candidates"] if c["snapshot"] is not None]

        def rule_by_snapshot(c):
            r = c.get("rule")
            return isinstance(r, dict) and bool(r.get("weeks")) and r["weeks"][0] <= c["snapshot"]["week"]
        found.sort(key=lambda c: (c["snapshot"].get("source") == "rule", not rule_by_snapshot(c),
                                  -c["snapshot"]["score"], sha256_hex(f"{tie_salt}|{c['key']}")))
        return (found if top_n is None else found[:top_n]), len(found)
    ew.ordered_candidates = rulefirst
    print(f"ABLATION {a.tag}: candidate order = rulefirst", flush=True)
if a.candidates != "snapshot":
    from mycelic.collective.edge.weeks import week_sunday
    from mycelic.collective.evaluate.baselines import closing_date
    shipped = ew.ordered_candidates
    WIN = 6

    def wk_add(week, n):
        y, w = week.split("-W"); w = int(w) + n
        return f"{y}-W{w:02d}" if 1 <= w <= 52 else None

    def expanded(result, *, tie_salt=ew.TIE_SALT, top_n=ew.TOP_N):
        base, detected = shipped(result, tie_salt=tie_salt, top_n=None)
        out = list(base)
        pack = expanded.pack
        for c in result["candidates"]:
            snap = c.get("snapshot")
            if snap is not None:
                weeks = list(c.get("candidate_weeks") or [])
                first, sites = snap["week"], list(snap["supporting_sites"])
                asks = [w for w in weeks if w > first]          # every later week the candidate persists
            elif a.candidates == "persist+rule" and isinstance(c.get("rule"), dict) and c["rule"].get("weeks"):
                weeks = list(c["rule"]["weeks"])
                sites = [s["site"] if isinstance(s, dict) else s for s in c["rule"].get("sites", [])]
                start = weeks[0]
                asks = [w for w in weeks if w >= (wk_add(start, WIN - 1) or start)][::WIN]
            else:
                continue
            for w in asks:
                start = wk_add(w, -(WIN - 1))
                if start is None:
                    continue
                synth = {**c, "snapshot": {"week": w, "as_of": closing_date(pack, w), "window": [start, w],
                                           "score": (snap or {}).get("score", 0.0), "supporting_sites": sites,
                                           "synthetic_reask": True}}
                out.append(synth)
        out.sort(key=lambda c: (-c["snapshot"]["score"], c["snapshot"]["week"], c["key"]))
        return (out if top_n is None else out[:top_n]), len(out)

    from mycelic.collective.packs.loader import load_pack
    expanded.pack = load_pack(str(pack)) if a.pack_override else load_pack(str(ew.PACK_DIR))
    ew.ordered_candidates = expanded
    print(f"ABLATION {a.tag}: candidates={a.candidates}", flush=True)
from research.e2e_ingest.__main__ import main
sys.exit(main(rest))

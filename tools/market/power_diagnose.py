#!/usr/bin/env python
"""Why the two comparators find what they find in one world of the power check (``mycelic.collective.pilot.power``):
the diagnostic behind items 3 and 4 of ``docs/collective/POWER.md``.

    python tools/market/power_diagnose.py --pack docs/collective/replay/vehicles/pack
        --background docs/collective/power/vehicle-background.json --years 2 --seed 1
        [--lookback-weeks 26,104] [--out FILE]

It rebuilds one world exactly as the check does (``power.build_world``, the check's defaults unless told), computes
model-free R's cells as the audit does, and runs P and PRR through the audit's own code (``comparator_results``). It
keeps what the audit keeps: alerts from the first evaluated week on. X and S are not run, so a world takes seconds.

* **P, the pooled detectors:** candidates per evaluated week; how many candidate keys carry the high-base-rate flag
  in their snapshot (the first alert's step, or the first candidate step), against whether they alerted; and, per
  sextupling, whether P found it, whether it was a candidate in the look-back, its alert weeks, its flag and score.
* **PRR:** per ramp, its alerts (week index), the ones dropped before the first evaluated week, and the lead of the
  first kept alert in each look-back.

The world is synthetic, as in the check it explains.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mycelic.collective.edge.weeks import week_monday  # noqa: E402
from mycelic.collective.evaluate.baselines import closing_date, r_mf_cells  # noqa: E402
from mycelic.collective.experiments.openfda_replay import replay_weeks  # noqa: E402
from mycelic.collective.packs.connector import map_rows  # noqa: E402
from mycelic.collective.pilot import audit as A  # noqa: E402
from mycelic.collective.pilot import power as P  # noqa: E402

KIND = "power_diagnosis"
LABEL = "synthetic: one generated world of the power check, the same author's plants and detectors"
DEFAULTS = {"rates": [0.1, 0.5, 2.0], "plants": 10, "sextuplings": 10, "ramp_weeks": 104, "sextuple_weeks": 13,
            "stagger_weeks": 26}


def _lead(available: str, opened: str) -> int:
    return (date.fromisoformat(opened) - date.fromisoformat(available)).days


def cooling_candidate_weeks(candidate: Mapping[str, Any], weeks: Sequence[str], cooldown: int) -> list[str]:
    """The weeks a key was a candidate but still cooling from an earlier alert, so it could not alert: the walk's
    cooldown replayed from the key's candidate and alert weeks."""
    cand, alerted = set(candidate["candidate_weeks"]), set(candidate["alert_weeks"])
    out: list[str] = []
    state: int | None = None
    for w in weeks:
        if state is not None:
            if w in cand:
                state = 0
            else:
                state += 1
                if state >= cooldown:
                    state = None
        if state is not None and w in cand:
            out.append(w)
        if w in alerted and cooldown > 0:
            state = 0
    return out


def diagnose(pack: Any, bg: Mapping[str, Any], *, years: int, seed: int, lookbacks: Sequence[int],
             tie_salt: str = "pilot", **world: Any) -> dict[str, Any]:
    settings = {**DEFAULTS, **world}
    rows, planted, world_weeks = P.build_world(pack, bg, years=years, seed=seed, **settings)
    records = list(map_rows(rows, pack, synthetic=True).records)
    days = sorted(r["received_date"][:10] for r in records)
    weeks = replay_weeks(days[0], days[-1])
    d = pack.detectors
    first = d["window_weeks"] + d["min_history_weeks"] - 1                 # the audit's first evaluated week
    master = A._master_data(pack, records, sorted({r["site"] for r in records}))
    cells = r_mf_cells(pack, records, master_data=master, last_week=weeks[-1])
    raw = A.comparator_results(pack, cells, weeks, as_of=closing_date(pack, weeks[-1]), tie_salt=tie_salt)
    pos = {w: i for i, w in enumerate(weeks)}
    kept = {name: [a for a in raw[name]["alerts"] if pos[a["week"]] >= first] for name in A.COMPARATORS}
    etype = bg["entities"]["type"]

    def key(p: P.Planted) -> str:
        return f"{etype}:{p.entity_id}:{p.predicate}"

    def opened(p: P.Planted) -> str:
        return week_monday(world_weeks[p.open_week]).isoformat()

    def in_lookback(week: str, p: P.Planted, lb: int) -> bool:
        return 0 < _lead(closing_date(pack, week), opened(p)) <= 7 * lb

    def found(name: str, p: P.Planted, lb: int) -> bool:
        return any(a["key"] == key(p) and in_lookback(a["week"], p, lb) for a in kept[name])

    # P: candidates, the high-base-rate flag, and each sextupling
    result = raw["P"]
    per_week = [w["candidates"] for w in result["weeks"] if pos[w["week"]] >= first]
    by_key = {c["key"]: c for c in result["candidates"]}
    crosstab: dict[str, int] = {}
    for c in result["candidates"]:
        snap = c["snapshot"]
        flag = None if snap is None else int(snap["features"]["high_base_rate"])
        name = f"alerted={bool(c['alert_weeks'])} high_base_rate={flag}"
        crosstab[name] = crosstab.get(name, 0) + 1
    sextuplings = []
    for p in planted:
        if p.kind != P.SEXTUPLE or p.role != P.PLANT:
            continue
        c = by_key.get(key(p))
        snap = None if c is None else c["snapshot"]
        cand = [] if c is None else c["candidate_weeks"]
        cooling = [] if c is None else cooling_candidate_weeks(c, weeks, d["cooldown_weeks"])
        sextuplings.append({
            "outcome_id": p.outcome_id, "key": key(p), "opened": opened(p),
            "found": {str(lb): found("P", p, lb) for lb in lookbacks},
            "candidate_weeks_in_lookback": {str(lb): sum(in_lookback(w, p, lb) for w in cand) for lb in lookbacks},
            "cooling_candidate_weeks_in_lookback": {str(lb): sum(in_lookback(w, p, lb) for w in cooling)
                                                    for lb in lookbacks},
            "candidate_weeks": len(cand), "alert_weeks": [] if c is None else c["alert_weeks"],
            "high_base_rate": None if snap is None else int(snap["features"]["high_base_rate"]),
            "score": None if snap is None else snap["score"]})
    # PRR: each ramp's alerts, kept and dropped
    ramps = []
    for p in planted:
        if p.kind != P.RAMP or p.role != P.PLANT:
            continue
        mine = [a for a in raw["PRR"]["alerts"] if a["key"] == key(p)]
        leads = {}
        for lb in lookbacks:
            hits = [_lead(closing_date(pack, a["week"]), opened(p)) for a in mine
                    if pos[a["week"]] >= first and in_lookback(a["week"], p, lb)]
            leads[str(lb)] = max(hits) if hits else None
        ramps.append({"outcome_id": p.outcome_id, "rate": p.rate, "key": key(p), "opened": opened(p),
                      "open_week_index": p.open_week, "alert_week_indices": [pos[a["week"]] for a in mine],
                      "dropped_before_first_evaluated": sum(1 for a in mine if pos[a["week"]] < first),
                      "found": {lb: v is not None for lb, v in leads.items()}, "first_lead_days": leads})
    return {"kind": KIND, "label": LABEL, "pack": pack.id, "years": years, "seed": seed,
            "settings": {**settings, "lookback_weeks": list(lookbacks), "tie_salt": tie_salt},
            "world": {"records": len(records), "weeks": len(weeks), "first_evaluated_index": first,
                      "first_evaluated_week": weeks[first]},
            "P": {"alerts_kept": len(kept["P"]), "candidates_per_week_median": statistics.median(per_week),
                  "candidates_per_week_range": [min(per_week), max(per_week)],
                  "candidate_keys": len(result["candidates"]), "snapshot_flags": dict(sorted(crosstab.items())),
                  "sextuplings": sextuplings},
            "PRR": {"alerts_kept": len(kept["PRR"]),
                    "alerts_dropped_before_first_evaluated": len(raw["PRR"]["alerts"]) - len(kept["PRR"]),
                    "ramps": ramps}}


def summary(doc: Mapping[str, Any]) -> list[str]:
    p, prr, w = doc["P"], doc["PRR"], doc["world"]
    lbs = [str(lb) for lb in doc["settings"]["lookback_weeks"]]
    lines = [f"world {doc['years']} years, seed {doc['seed']}: {w['records']} records, {w['weeks']} weeks, first "
             f"evaluated {w['first_evaluated_week']} (index {w['first_evaluated_index']})",
             f"P: {p['alerts_kept']} alerts; candidates a week median {p['candidates_per_week_median']}, range "
             f"{p['candidates_per_week_range'][0]} to {p['candidates_per_week_range'][1]}; {p['candidate_keys']} "
             f"candidate keys: " + ", ".join(f"{k} {v}" for k, v in p["snapshot_flags"].items())]
    sx = p["sextuplings"]
    for lb in lbs:
        missed = [s for s in sx if not s["found"][lb]]
        lines.append(f"P sextuplings, look-back {lb}: found {len(sx) - len(missed)} of {len(sx)}; of the missed, a "
                     f"candidate in the look-back {sum(1 for s in missed if s['candidate_weeks_in_lookback'][lb])}, "
                     f"never a candidate {sum(1 for s in missed if not s['candidate_weeks'])}, cooling while a "
                     f"candidate there {sum(1 for s in missed if s['cooling_candidate_weeks_in_lookback'][lb])}, "
                     f"alerted outside it {sum(1 for s in missed if s['alert_weeks'])}")
    lines.append("P sextupling snapshots with the high-base-rate flag: "
                 f"{sum(1 for s in sx if s['high_base_rate'] == 1)} of {len(sx)}; scores "
                 + ", ".join("n/a" if s["score"] is None else f"{s['score']:.3f}" for s in sx))
    lines.append(f"PRR: {prr['alerts_kept']} alerts kept, {prr['alerts_dropped_before_first_evaluated']} dropped "
                 "before the first evaluated week")
    for rate in sorted({r["rate"] for r in prr["ramps"]}):
        rs = [r for r in prr["ramps"] if r["rate"] == rate]
        for lb in lbs:
            leads = [r["first_lead_days"][lb] for r in rs if r["first_lead_days"][lb] is not None]
            span = f"; first-alert leads {min(leads) / 7:.1f} to {max(leads) / 7:.1f} weeks" if leads else ""
            lines.append(f"PRR ramps at {rate:g} a week, look-back {lb}: found {len(leads)} of {len(rs)}{span}")
        lines.append(f"PRR ramps at {rate:g} a week: {sum(1 for r in rs if r['dropped_before_first_evaluated'])} "
                     f"of {len(rs)} alerted before the first evaluated week")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--pack", required=True)
    p.add_argument("--background", required=True)
    p.add_argument("--years", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--lookback-weeks", default="26,104")
    p.add_argument("--rates", default=",".join(f"{r:g}" for r in DEFAULTS["rates"]))
    for name in ("plants", "sextuplings", "ramp_weeks", "sextuple_weeks", "stagger_weeks"):
        p.add_argument(f"--{name.replace('_', '-')}", type=int, default=DEFAULTS[name])
    p.add_argument("--tie-salt", default="pilot")
    p.add_argument("--out", help="write the full diagnosis as JSON here")
    args = p.parse_args(argv)
    pack = A._load(args.pack)
    bg = P.check_background(json.loads(Path(args.background).read_text(encoding="utf-8")), pack)
    lookbacks = [int(v) for v in args.lookback_weeks.split(",") if v.strip()]
    world = {name: getattr(args, name) for name in DEFAULTS if name != "rates"}
    doc = diagnose(pack, bg, years=args.years, seed=args.seed, lookbacks=lookbacks, tie_salt=args.tie_salt,
                   rates=[float(v) for v in args.rates.split(",") if v.strip()], **world)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("\n".join(summary(doc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

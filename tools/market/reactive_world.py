#!/usr/bin/env python
"""Reactive-world check of the pilot audit's two chance nulls, on synthetic alert timelines.

    python tools/market/reactive_world.py [--seeds 20] [--per-seed]

The pilot audit scores a channel's alerts against the outcomes with ``score_channel`` and reports two circular-shift
nulls: the audited one (``expected_found``, ``p_value``), which rotates every alert on an outcome's key, and the
corrected one (``expected_found_excluding_own_post``, ``p_value_excluding_own_post``), which first leaves out the
alerts on the outcome's key from its opening to ``post`` weeks after. This tool builds alert timelines whose truth is
known and scores them with that same function, seed by seed. Every world is synthetic: 40 outcomes, each on its own
key, opened on random days in 87 evaluated weeks, with look-back and post windows of 26 weeks (the vehicle replays'
settings). No detector runs and no real record is read.

- ``reactive``: alerts on an outcome's key come only after it opens (complaints reacting: each of the 12 alert weeks
  from its opening, with probability 0.15), plus background alerts on 40 keys no outcome is about (each week with
  probability 0.01). There is no pre-signal: nothing is found, and chance finds nothing.
- ``background``: ``reactive`` plus background alerts on the outcome keys too, at the same rate. Still no pre-signal,
  so the found count is what chance gives.
- ``presignal``: ``background`` plus a real pre-signal on every other outcome: each of the 8 alert weeks before it
  opens, with probability 0.3.

Each part of a world draws from its own random stream, so the three worlds of one seed share their openings,
reactions and background, and differ only in what they add. It prints, per world, the means over the seeds of the
found count and of each null's expected count, the mean of each null's p, and in how many seeds each p is below 0.05.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mycelic.collective.pilot.audit import Outcome, score_channel  # noqa: E402

LABEL = "synthetic: alert timelines generated with a known truth; no detector ran and no real data was read"
WORLDS = ("reactive", "background", "presignal")
SEEDS = 20
ALPHA = 0.05
OUTCOMES = 40
NOISE_KEYS = 40
EVALUATED_WEEKS, LOOKBACK, POST = 87, 26, 26          # the vehicle replays' settings
AVAILABLE_FIRST = date(2023, 6, 4)
REACT_WEEKS, REACT_P = 12, 0.15
BACKGROUND_P = 0.01
PRE_WEEKS, PRE_P = 8, 0.3
SCORED = ("found", "expected_found", "p_value", "expected_found_excluding_own_post", "p_value_excluding_own_post",
          "alerts")


def _alert(index: int, key: str) -> dict[str, Any]:
    day = AVAILABLE_FIRST + timedelta(days=7 * index)
    year, week, _ = day.isocalendar()
    entity_type, entity_id, predicate = key.split(":")
    return {"week": f"{year:04d}-W{week:02d}", "available_date": day.isoformat(), "rank": 1, "score": 1.0,
            "key": key, "entity_type": entity_type, "entity_id": entity_id, "predicate": predicate, "sites": [],
            "record_refs": []}


def _weeks(rng: random.Random, indices: Iterable[int], p: float) -> list[int]:
    """The alert weeks among ``indices`` (inside the evaluated weeks) that get an alert, each with probability p."""
    return [i for i in indices if 0 <= i < EVALUATED_WEEKS and rng.random() < p]


def world(name: str, seed: int) -> tuple[list[Outcome], list[dict[str, Any]]]:
    """One world's outcomes and alerts (alert week i is available on ``AVAILABLE_FIRST`` plus i weeks)."""
    if name not in WORLDS:
        raise ValueError(f"world must be one of {', '.join(WORLDS)}")
    streams = {part: random.Random(f"{seed}:{part}") for part in ("open", "react", "background", "pre", "noise")}
    outcomes: list[Outcome] = []
    alerts: list[dict[str, Any]] = []
    for n in range(OUTCOMES):
        opened = AVAILABLE_FIRST + timedelta(days=streams["open"].randint(1, 7 * EVALUATED_WEEKS - 1))
        outcomes.append(Outcome(f"O{n:02d}", opened.isoformat(), "vehicle", f"V{n:02d}", None))
        first = -(-(opened - AVAILABLE_FIRST).days // 7)     # the first alert week available on or after opening
        key = f"vehicle:V{n:02d}:engine"
        alerts += [_alert(i, key) for i in _weeks(streams["react"], range(first, first + REACT_WEEKS), REACT_P)]
        if name != "reactive":
            alerts += [_alert(i, key) for i in _weeks(streams["background"], range(EVALUATED_WEEKS), BACKGROUND_P)]
        if name == "presignal" and n % 2:
            alerts += [_alert(i, key) for i in _weeks(streams["pre"], range(first - PRE_WEEKS, first), PRE_P)]
    for n in range(NOISE_KEYS):
        alerts += [_alert(i, f"vehicle:N{n:02d}:engine")
                   for i in _weeks(streams["noise"], range(EVALUATED_WEEKS), BACKGROUND_P)]
    return outcomes, alerts


def score(name: str, seed: int) -> dict[str, Any]:
    outcomes, alerts = world(name, seed)
    s = score_channel(outcomes, alerts, lookback=LOOKBACK, post=POST, available_first=AVAILABLE_FIRST,
                      evaluated_weeks=EVALUATED_WEEKS)["summary"]
    return {"seed": seed, **{k: s[k] for k in SCORED}}


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 3)


def check(seeds: int = SEEDS) -> dict[str, Any]:
    worlds = {}
    for name in WORLDS:
        rows = [score(name, seed) for seed in range(1, seeds + 1)]
        worlds[name] = {
            "mean_found": _mean([r["found"] for r in rows]),
            "mean_expected_found": _mean([r["expected_found"] for r in rows]),
            "mean_expected_found_excluding_own_post": _mean([r["expected_found_excluding_own_post"] for r in rows]),
            "mean_p_value": _mean([r["p_value"] for r in rows]),
            "mean_p_value_excluding_own_post": _mean([r["p_value_excluding_own_post"] for r in rows]),
            "p_value_below_alpha": sum(1 for r in rows if r["p_value"] < ALPHA),
            "p_value_excluding_own_post_below_alpha": sum(1 for r in rows if r["p_value_excluding_own_post"] < ALPHA),
            "mean_alerts": _mean([r["alerts"] for r in rows]),
            "per_seed": rows}
    return {"kind": "reactive_world_check", "label": LABEL, "seeds": seeds, "alpha": ALPHA,
            "settings": {"outcomes": OUTCOMES, "noise_keys": NOISE_KEYS, "evaluated_weeks": EVALUATED_WEEKS,
                         "lookback_weeks": LOOKBACK, "post_weeks": POST, "react_weeks": REACT_WEEKS,
                         "react_p": REACT_P, "background_p": BACKGROUND_P, "pre_weeks": PRE_WEEKS, "pre_p": PRE_P},
            "worlds": worlds}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reactive_world.py", description=__doc__.split("\n")[0])
    p.add_argument("--seeds", type=int, default=SEEDS)
    p.add_argument("--per-seed", action="store_true", help="also print each seed's numbers")
    args = p.parse_args(argv)
    if args.seeds < 1:
        print("--seeds must be at least 1", file=sys.stderr)
        return 2
    result = check(args.seeds)
    if not args.per_seed:
        for w in result["worlds"].values():
            del w["per_seed"]
    print(json.dumps(result, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

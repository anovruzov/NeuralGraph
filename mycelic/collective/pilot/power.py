"""The power check: could the audit's channels see a planted signal of a given size at all?

    python -m mycelic.collective.pilot.power run --pack P --background FILE --out DIR
        [--rates 0.1,0.5,2.0] [--years 2,4] [--seeds 1,2,3] [--plants 10] [--sextuplings 10]
        [--lookback-weeks 26] [--threshold 0.8] [--max-control-share 0.2] [--workers 1] [--tie-salt pilot]

It takes ``--dry-run`` (the common contract). It exits 0 when every ramp cell passes the gate below, 1 when one does
not, and 2 on a usage error.

**Why.** The vehicle replays found nothing beyond chance, and no one had first asked whether the detectors could find a
signal of the size the data carry. This asks it on synthetic data, before a replay.

**The background** (``--background``, a JSON file; ``tools/market/vehicle_power.py`` writes the vehicle one). Each world
draws, from its seed: per-entity weekly rates (a gamma with the file's shape, scaled so they sum to
``weekly_volume``); site weights proportional to ``1 / rank ** skew``; and the failure shares of ``predicates``. Each
week, a Poisson number of records is split over entities, failures and sites by those weights. Every record carries one
code and a one-line narrative naming the failure. The rates do not change over time.

**The plants**, on distinct entities of one world, each with an outcome on its entity and failure:
- **ramps**, ``--plants`` per rate: on an entity whose total rate lies in ``plant_entity_rate``, a failure drawn by its
  share among the failures with a specific code. Extra records rise linearly from 0 to the rate a week over the
  ``--ramp-weeks`` before the outcome opens (cut at the export's first week), 52 x rate records in all for two years;
- **sextuplings**, ``--sextuplings`` per world: on a key whose background rate lies in ``sextuple_key_rate``, five
  times that rate on top for the ``--sextuple-weeks`` before the outcome, so the key runs at six times its rate.
Each plant has a **control**: an unplanted key of the same failure from the same band, on another entity, with an
outcome opened the same day. Outcomes open in the export's last ``--stagger-weeks``, spread evenly.

**Power** is, per channel, the share of plants with an alert on their key available in the look-back before the
outcome opens (the audit's own "found"). The **control share** is the same share over the controls: what the channel
finds where nothing was planted. Both come with 95% Wilson intervals, per look-back in ``--lookback-weeks``.

**The gate**, per ramp rate and history, at the first look-back listed: the best channel's power must be at least
``--threshold``, counting only channels whose control share is at most ``--max-control-share`` (a channel that flags
most keys would find any plant and still tell nothing). A replay's choice file runs the check with the rate, history
and look-back it assumes, and cites a passing run. Sextuplings are reported, not gated.

The worlds are synthetic, and the plants and the detectors share an author: a pass says the instrument could see a
signal of that size in data shaped like this; it says nothing about whether real data hold one.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import stats
from ..edge.weeks import iso_week, week_monday
from ..experiments.common import DryRun, UsageError, fail, split_list, write_json_atomic
from ..packs.canonical import Canonicaliser
from ..packs.loader import FrozenPack, PackError, is_builtin_ref, load_pack
from . import audit as A

CLI = "pilot.power"
KIND = "pilot_power"
SCHEMA_VERSION = 1
BACKGROUND_KIND = "power_background"
LABEL = ("synthetic power check: generated records and planted signals written by the same author as the detectors; "
         "it measures whether the channels could see a signal of this size, not whether real data hold one")
RAMP, SEXTUPLE = "ramp", "sextuple"
PLANT, CONTROL = "plant", "control"
SEXTUPLE_FACTOR = 6
POISSON_CHUNK = 30.0
BACKGROUND_KEYS = ("kind", "schema_version", "label", "start", "weekly_volume", "sites", "entities", "predicates",
                   "plant_entity_rate", "sextuple_key_rate", "sources")


class PowerError(ValueError):
    pass


@dataclass(frozen=True)
class Planted:
    outcome_id: str
    kind: str
    role: str
    rate: float | None
    entity_id: str
    predicate: str
    open_week: int
    background_rate: float
    records: int


# --------------------------------------------------------------------------------------------------- the background

def _band(value: Any, what: str) -> tuple[float, float]:
    if (not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, (int, float)) for v in value)
            or not 0 <= value[0] < value[1]):
        raise PowerError(f"background: {what} must be [low, high] with 0 <= low < high")
    return float(value[0]), float(value[1])


def check_background(bg: Any, pack: FrozenPack) -> dict[str, Any]:
    """The background file, checked against the pack: every key present, the entity type and predicates the pack's,
    and a generated id that resolves."""
    if not isinstance(bg, dict) or sorted(bg) != sorted(BACKGROUND_KEYS):
        raise PowerError(f"background: needs exactly the keys {', '.join(BACKGROUND_KEYS)}")
    if bg["kind"] != BACKGROUND_KIND or bg["schema_version"] != 1:
        raise PowerError(f"background: kind must be {BACKGROUND_KIND} and schema_version 1")
    try:
        start = date.fromisoformat(bg["start"])
    except (TypeError, ValueError):
        raise PowerError("background: start must be YYYY-MM-DD") from None
    if start.weekday() != 0:
        raise PowerError("background: start must be a Monday")
    if not isinstance(bg["weekly_volume"], (int, float)) or not bg["weekly_volume"] > 0:
        raise PowerError("background: weekly_volume must be > 0")
    sites, ents = bg["sites"], bg["entities"]
    if (not isinstance(sites, dict) or not isinstance(sites.get("count"), int) or sites["count"] < 2
            or not isinstance(sites.get("skew"), (int, float)) or not isinstance(sites.get("id_format"), str)):
        raise PowerError("background: sites needs count (>= 2), skew and id_format")
    if (not isinstance(ents, dict) or ents.get("type") not in pack.entity_types
            or not isinstance(ents.get("count"), int) or ents["count"] < 2
            or not isinstance(ents.get("gamma_shape"), (int, float)) or not ents["gamma_shape"] > 0
            or not isinstance(ents.get("id_format"), str)):
        raise PowerError("background: entities needs a pack entity type, count (>= 2), gamma_shape and id_format")
    canon = Canonicaliser(pack)
    for n in (1, ents["count"]):
        eid = ents["id_format"].format(n=n)
        m = canon.resolve_exact(ents["type"], eid)
        if m is None or m.entity_id != eid:
            raise PowerError(f"background: entity id {eid!r} is not a canonical {ents['type']} of pack {pack.id}")
    preds = bg["predicates"]
    if (not isinstance(preds, dict) or not preds
            or any(p not in pack.predicates or not isinstance(w, (int, float)) or w < 0 for p, w in preds.items())):
        raise PowerError("background: predicates must map pack predicates to weights >= 0")
    missing = [p for p in sorted(preds) if p not in _codes(pack)]
    if missing:
        raise PowerError(f"background: no pack code for {', '.join(missing)}")
    _band(bg["plant_entity_rate"], "plant_entity_rate")
    _band(bg["sextuple_key_rate"], "sextuple_key_rate")
    return bg


def _codes(pack: FrozenPack) -> dict[str, tuple[str, bool]]:
    """Each predicate's code, a specific one first: ``predicate -> (code, specific)``."""
    out: dict[str, tuple[str, bool]] = {}
    for code in sorted(pack.codes):
        c = pack.codes[code]
        if c.predicate not in out or (c.specific and not out[c.predicate][1]):
            out[c.predicate] = (code, c.specific)
    return out


def poisson(rng: random.Random, lam: float) -> int:
    """A Poisson draw (Knuth's method, in chunks of 30 so exp never underflows)."""
    n = 0
    while lam > POISSON_CHUNK:
        n += poisson(rng, POISSON_CHUNK)
        lam -= POISSON_CHUNK
    if lam <= 0:
        return n
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return n + k
        k += 1


def _cum(weights: Sequence[float]) -> list[float]:
    out, total = [], 0.0
    for w in weights:
        total += w
        out.append(total)
    return out


# --------------------------------------------------------------------------------------------------- one world

def build_world(pack: FrozenPack, bg: Mapping[str, Any], *, years: int, seed: int, rates: Sequence[float],
                plants: int, sextuplings: int, ramp_weeks: int, sextuple_weeks: int,
                stagger_weeks: int) -> tuple[list[dict[str, Any]], list[Planted], list[str]]:
    """A world's export rows (in the pack's mapping), its plants and controls, and its weeks."""
    rng = random.Random(f"power:{seed}:{years}")
    nweeks = 52 * years
    if stagger_weeks < 1 or stagger_weeks >= nweeks:
        raise PowerError("stagger_weeks must be at least 1 and below the export's weeks")
    start = date.fromisoformat(bg["start"])
    weeks = [iso_week(start + timedelta(days=7 * i)) for i in range(nweeks)]
    s, e = bg["sites"], bg["entities"]
    site_ids = [s["id_format"].format(n=i + 1) for i in range(s["count"])]
    site_cum = _cum([1.0 / (i + 1) ** s["skew"] for i in range(s["count"])])
    etype = e["type"]
    ids = [e["id_format"].format(n=i + 1) for i in range(e["count"])]
    mean = bg["weekly_volume"] / e["count"]
    draws = [rng.gammavariate(e["gamma_shape"], mean / e["gamma_shape"]) for _ in ids]
    scale = bg["weekly_volume"] / math.fsum(draws)
    rate = {eid: d * scale for eid, d in zip(ids, draws)}
    codes = _codes(pack)
    preds = sorted(p for p in bg["predicates"] if bg["predicates"][p] > 0)
    total_w = math.fsum(bg["predicates"][p] for p in preds)
    share = {p: bg["predicates"][p] / total_w for p in preds}
    specific = [p for p in preds if codes[p][1]]
    lang = pack.languages[0]
    phrase = {p: (pack.predicates[p].lexicon.get(lang) or (pack.predicates[p].label.lower(),))[0] for p in preds}
    records: list[tuple[str, str, str, str]] = []           # (day, site, entity id, predicate)

    def add(w: int, eid: str, p: str, n: int) -> None:
        for site in rng.choices(site_ids, cum_weights=site_cum, k=n):
            records.append(((start + timedelta(days=7 * w + rng.randrange(7))).isoformat(), site, eid, p))

    ent_cum = _cum([rate[eid] for eid in ids])
    pred_cum = _cum([share[p] for p in preds])
    for w in range(nweeks):
        n = poisson(rng, bg["weekly_volume"])
        for eid, p, site in zip(rng.choices(ids, cum_weights=ent_cum, k=n),
                                rng.choices(preds, cum_weights=pred_cum, k=n),
                                rng.choices(site_ids, cum_weights=site_cum, k=n)):
            records.append(((start + timedelta(days=7 * w + rng.randrange(7))).isoformat(), site, eid, p))

    # the plants: ramps on busy entities, sextuplings on keys of a given background rate, each with a control
    lo, hi = bg["plant_entity_rate"]
    pool = [eid for eid in ids if lo <= rate[eid] <= hi]
    rng.shuffle(pool)
    n_ramps = len(rates) * plants
    if len(pool) < 2 * n_ramps:
        raise PowerError(f"only {len(pool)} entities have a rate in plant_entity_rate; {2 * n_ramps} are needed")
    used = set(pool[:2 * n_ramps])
    spec_cum = _cum([share[p] for p in specific])
    # (kind, rate, name, (plant entity, failure), (control entity, failure))
    pairs: list[tuple[str, float | None, str, tuple[str, str], tuple[str, str]]] = []
    for j in range(n_ramps):
        r = rates[j // plants]
        p = rng.choices(specific, cum_weights=spec_cum, k=1)[0]
        pairs.append((RAMP, r, f"{RAMP}-{r:g}-{j % plants + 1:02d}", (pool[2 * j], p), (pool[2 * j + 1], p)))
    klo, khi = bg["sextuple_key_rate"]
    keys = [(eid, p) for eid in ids if eid not in used for p in specific if klo <= rate[eid] * share[p] <= khi]
    rng.shuffle(keys)
    for j in range(sextuplings):
        plant_key = next((k for k in keys if k[0] not in used), None)
        if plant_key is not None:
            used.add(plant_key[0])
        control_key = (next((k for k in keys if k[0] not in used and k[1] == plant_key[1]), None)
                       or next((k for k in keys if k[0] not in used), None)) if plant_key is not None else None
        if plant_key is None or control_key is None:
            raise PowerError(f"too few keys with a rate in sextuple_key_rate on unused entities for {sextuplings} "
                             "sextuplings and their controls")
        used.add(control_key[0])
        pairs.append((SEXTUPLE, None, f"{SEXTUPLE}-{j + 1:02d}", plant_key, control_key))
    order = list(range(len(pairs)))
    rng.shuffle(order)
    planted: list[Planted] = []
    for slot, j in enumerate(order):
        kind, r, name, (plant_e, plant_p), (control_e, control_p) = pairs[j]
        plant_b, control_b = rate[plant_e] * share[plant_p], rate[control_e] * share[control_p]
        open_week = nweeks - stagger_weeks + (slot * stagger_weeks) // len(pairs)
        count = 0
        if kind == RAMP:
            assert r is not None
            first = open_week - ramp_weeks
            for w in range(max(first, 0), open_week):
                k = poisson(rng, r * (w - first + 1) / ramp_weeks)
                add(w, plant_e, plant_p, k)
                count += k
        else:
            for w in range(max(open_week - sextuple_weeks, 0), open_week):
                k = poisson(rng, (SEXTUPLE_FACTOR - 1) * plant_b)
                add(w, plant_e, plant_p, k)
                count += k
        planted.append(Planted(name, kind, PLANT, r, plant_e, plant_p, open_week, plant_b, count))
        planted.append(Planted(f"{CONTROL}-{name}", kind, CONTROL, r, control_e, control_p, open_week, control_b, 0))
    records.sort()
    mapping = pack.mapping()
    persons = {f: None for f in sorted(mapping["persons"])}
    rows = []
    for n, (day, site, eid, p) in enumerate(records, start=1):
        record = {"record_ref": f"r{n:07d}", "site": site, "received_date": day, "language": None,
                  "codes": [codes[p][0]], "entities": {etype: [eid]},
                  "narrative": f"The owner reports a problem with the {phrase[p]}.", "persons": persons,
                  "reporter": None, "origin_ref": None, "origin_site": None}
        rows.append(A.export_row(record, mapping))
    return rows, planted, weeks


def found_in_lookback(timeline: Sequence[Mapping[str, Any]], key: str, opened: str, lookback: int) -> bool:
    """The audit's "found": an alert on ``key`` available in the ``lookback`` weeks before ``opened``."""
    day = date.fromisoformat(opened)
    low = day - timedelta(days=7 * lookback)
    return any(key in t["keys"] and low <= date.fromisoformat(t["available_date"]) < day for t in timeline)


def run_world(pack_ref: str, bg: Mapping[str, Any], *, years: int, seed: int, rates: Sequence[float], plants: int,
              sextuplings: int, ramp_weeks: int, sextuple_weeks: int, stagger_weeks: int,
              lookbacks: Sequence[int], tie_salt: str) -> dict[str, Any]:
    """One world through the audit (every channel, comparators included), scored per plant and look-back."""
    pack = _load(pack_ref)
    rows, planted, weeks = build_world(pack, bg, years=years, seed=seed, rates=rates, plants=plants,
                                       sextuplings=sextuplings, ramp_weeks=ramp_weeks, sextuple_weeks=sextuple_weeks,
                                       stagger_weeks=stagger_weeks)
    etype = bg["entities"]["type"]
    outcomes = [A.Outcome(p.outcome_id, week_monday(weeks[p.open_week]).isoformat(), etype, p.entity_id, p.predicate)
                for p in planted]
    doc = A.audit(pack, rows, outcomes, lookback=max(lookbacks), tie_salt=tie_salt, synthetic=True, comparators=True)
    if doc["outcomes"]["out_of_scope"]:
        raise PowerError(f"world years={years} seed={seed}: outcomes out of scope {doc['outcomes']['out_of_scope']}")
    names = A.channel_names(doc)
    scored = []
    for p, o in zip(planted, outcomes):
        found: dict[str, dict[str, bool] | None] = {}
        for name in names:
            timeline = doc["channels"][name].get("alert_timeline")
            found[name] = None if timeline is None else {
                str(lb): found_in_lookback(timeline, o.key, o.opened, lb) for lb in lookbacks}
        scored.append({**asdict(p), "opened": o.opened, "found": found})
    return {"years": years, "seed": seed, "weeks": {"first": weeks[0], "last": weeks[-1],
                                                     "evaluated_from": doc["weeks"]["evaluated_from"]},
            "records": doc["export"]["records"], "sites": len(doc["export"]["sites"]),
            "channels": list(names),
            "alerts": {n: (doc["channels"][n]["summary"] or {}).get("alerts") for n in names},
            "reasons": {n: doc["channels"][n]["reason"] for n in names if doc["channels"][n]["reason"]},
            "plants": scored}


# --------------------------------------------------------------------------------------------------- the report

def _share(k: int, n: int) -> dict[str, Any]:
    ci = stats.wilson(k, n)
    return {"found": k, "of": n, "share": k / n if n else None, "ci95": list(ci) if ci else None}


def summarise(worlds: Sequence[Mapping[str, Any]], *, lookbacks: Sequence[int], threshold: float,
              max_control_share: float) -> dict[str, Any]:
    """Power and control share per (kind, rate, years), channel and look-back; the gate per ramp cell."""
    names: list[str] = []
    for w in worlds:
        names += [n for n in w["channels"] if n not in names]
    groups: dict[tuple[str, float | None, int], list[Mapping[str, Any]]] = {}
    for w in worlds:
        for p in w["plants"]:
            groups.setdefault((p["kind"], p["rate"], w["years"]), []).append(p)
    cells = []
    for kind, rate, years in sorted(groups, key=lambda g: (g[0] != RAMP, g[1] or 0.0, g[2])):
        items = groups[(kind, rate, years)]
        cell: dict[str, Any] = {"kind": kind, "rate": rate, "years": years,
                                "plants": sum(1 for p in items if p["role"] == PLANT),
                                "planted_records_mean": stats.mean([p["records"] for p in items
                                                                     if p["role"] == PLANT]),
                                "by_lookback": {}}
        for lb in lookbacks:
            per: dict[str, Any] = {}
            for name in names:
                runs = [p for p in items if p["found"].get(name) is not None]
                plant = [p for p in runs if p["role"] == PLANT]
                control = [p for p in runs if p["role"] == CONTROL]
                per[name] = None if not plant else {
                    "power": _share(sum(p["found"][name][str(lb)] for p in plant), len(plant)),
                    "control": _share(sum(p["found"][name][str(lb)] for p in control), len(control))}
            eligible = {n: v["power"]["share"] for n, v in per.items()
                        if v is not None and v["control"]["share"] is not None
                        and v["control"]["share"] <= max_control_share}
            best = max(eligible, key=lambda n: eligible[n]) if eligible else None     # ties: the first in order
            cell["by_lookback"][str(lb)] = {
                "channels": per, "best": best, "best_power": eligible.get(best) if best else None,
                "passes": (bool(best is not None and eligible[best] >= threshold) if kind == RAMP
                           else None)}                  # sextuplings are reported, not gated
        cells.append(cell)
    gate_lb = str(lookbacks[0])
    ramps = [c for c in cells if c["kind"] == RAMP]
    return {"channels": names, "cells": cells,
            "gate": {"lookback_weeks": lookbacks[0], "threshold": threshold,
                     "max_control_share": max_control_share,
                     "failing": [{"rate": c["rate"], "years": c["years"],
                                  "best": c["by_lookback"][gate_lb]["best"],
                                  "best_power": c["by_lookback"][gate_lb]["best_power"]}
                                 for c in ramps if not c["by_lookback"][gate_lb]["passes"]],
                     "passes": bool(ramps) and all(c["by_lookback"][gate_lb]["passes"] for c in ramps)}}


def _fmt(entry: Mapping[str, Any] | None) -> str:
    if entry is None:
        return "not run"
    pw, ct = entry["power"], entry["control"]
    return f"{pw['share']:.2f} ({pw['found']}/{pw['of']}); control {ct['found']}/{ct['of']}"


def render(doc: Mapping[str, Any]) -> str:
    s, bg = doc["settings"], doc["background"]
    names = doc["summary"]["channels"]
    gate = doc["summary"]["gate"]
    lines = ["# Power check", "", f"_{doc['label']}._", "",
             f"- Pack `{doc['pack']['id']}`; background `{bg['file']}` (sha256 `{bg['sha256'][:16]}`): "
             f"{bg['weekly_volume']:g} records a week over {bg['sites']} sites and {bg['entities']} entities.",
             f"- Worlds: {len(doc['worlds'])} ({', '.join(str(y) for y in s['years'])} years x seeds "
             f"{', '.join(str(x) for x in s['seeds'])}); per world {s['plants']} ramps per rate and "
             f"{s['sextuplings']} sextuplings, each with a control.",
             f"- Ramps rise linearly to the rate over {s['ramp_weeks']} weeks; sextuplings last "
             f"{s['sextuple_weeks']} weeks; outcomes open in the last {s['stagger_weeks']} weeks.",
             f"- Gate (look-back {gate['lookback_weeks']} weeks): the best channel with a control share of at most "
             f"{gate['max_control_share']:g} must find at least {gate['threshold']:g} of the plants. "
             f"**{'Passes' if gate['passes'] else 'Fails'}.**", ""]
    for lb in s["lookback_weeks"]:
        for kind, title in ((RAMP, "Ramps"), (SEXTUPLE, "Sextuplings")):
            cells = [c for c in doc["summary"]["cells"] if c["kind"] == kind]
            if not cells:
                continue
            lines += [f"## {title}, look-back {lb} weeks", "",
                      "| Rate a week | Years | Plants | Planted records (mean) | " + " | ".join(names)
                      + " | Best | Passes |", "|---" * (6 + len(names)) + "|"]
            for c in cells:
                b = c["by_lookback"][str(lb)]
                rate = "6x" if c["rate"] is None else f"{c['rate']:g}"
                verdict = "not gated" if b["passes"] is None else ("yes" if b["passes"] else "no")
                lines.append(f"| {rate} | {c['years']} | {c['plants']} | {c['planted_records_mean']:.1f} | "
                             + " | ".join(_fmt(b["channels"].get(n)) for n in names)
                             + f" | {b['best'] or 'none'} | {verdict} |")
            lines.append("")
    lines += ["Each channel cell: power (plants found of plants); control: found where nothing was planted.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------- CLI

def _load(ref: str) -> FrozenPack:
    try:
        return load_pack(ref if is_builtin_ref(ref) else Path(ref))
    except PackError as err:
        raise UsageError(f"pack: {err}") from None


def _numbers(value: str, kind: type, what: str) -> list[Any]:
    try:
        out = [kind(v) for v in split_list(value)]
    except ValueError:
        raise UsageError(f"{what}: not a list of numbers") from None
    if not out or any(v <= 0 for v in out) or len(set(out)) != len(out):
        raise UsageError(f"{what}: give distinct numbers > 0")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.pilot.power", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="plant signals in synthetic worlds and measure each channel's power")
    r.add_argument("--pack", required=True)
    r.add_argument("--background", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--rates", default="0.1,0.5,2.0", help="ramp rates, records a week at the ramp's end")
    r.add_argument("--years", default="2,4", help="history lengths in years")
    r.add_argument("--seeds", default="1,2,3")
    r.add_argument("--plants", type=int, default=10, help="ramps per rate per world")
    r.add_argument("--sextuplings", type=int, default=10, help="sextuplings per world")
    r.add_argument("--ramp-weeks", type=int, default=104)
    r.add_argument("--sextuple-weeks", type=int, default=13)
    r.add_argument("--stagger-weeks", type=int, default=26)
    r.add_argument("--lookback-weeks", default="26", help="look-backs to score; the gate uses the first")
    r.add_argument("--threshold", type=float, default=0.8)
    r.add_argument("--max-control-share", type=float, default=0.2)
    r.add_argument("--tie-salt", default="pilot")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--dry-run", action="store_true", help="say what would be read and written; touch nothing")
    args = p.parse_args(argv)
    if args.dry_run:
        dry = DryRun(f"{CLI} {args.command}")
        if not is_builtin_ref(args.pack) and not Path(args.pack).is_dir():
            dry.need(f"pack directory {args.pack}")
        if not Path(args.background).is_file():
            dry.need(f"file {args.background}")
        for name in ("power.json", "power.md"):
            dry.write(str(Path(args.out) / name))
        return dry.emit()
    try:
        rates = _numbers(args.rates, float, "--rates")
        years = _numbers(args.years, int, "--years")
        seeds = _numbers(args.seeds, int, "--seeds")
        lookbacks = list(dict.fromkeys(_numbers(args.lookback_weeks, int, "--lookback-weeks")))
        for name in ("plants", "ramp_weeks", "sextuple_weeks", "stagger_weeks", "workers"):
            if getattr(args, name) < 1:
                raise UsageError(f"--{name.replace('_', '-')} must be at least 1")
        if args.sextuplings < 0 or not 0 < args.threshold <= 1 or not 0 <= args.max_control_share <= 1:
            raise UsageError("--sextuplings must be >= 0, --threshold in (0, 1], --max-control-share in [0, 1]")
        pack = _load(args.pack)
        try:
            data = Path(args.background).read_bytes()
            bg = check_background(json.loads(data.decode("utf-8")), pack)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, PowerError):
                raise
            raise UsageError(f"cannot read {args.background} ({exc.__class__.__name__})") from None
        jobs = [(y, s) for y in years for s in seeds]
        kwargs = {"rates": rates, "plants": args.plants, "sextuplings": args.sextuplings,
                  "ramp_weeks": args.ramp_weeks, "sextuple_weeks": args.sextuple_weeks,
                  "stagger_weeks": args.stagger_weeks, "lookbacks": lookbacks, "tie_salt": args.tie_salt}
        if args.workers == 1:
            worlds = [run_world(args.pack, bg, years=y, seed=s, **kwargs) for y, s in jobs]
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as ex:
                futures = [ex.submit(run_world, args.pack, bg, years=y, seed=s, **kwargs) for y, s in jobs]
                worlds = [f.result() for f in futures]
    except (PowerError, UsageError, A.AuditError) as exc:
        return fail(str(exc))
    summary = summarise(worlds, lookbacks=lookbacks, threshold=args.threshold,
                        max_control_share=args.max_control_share)
    doc = {"kind": KIND, "schema_version": SCHEMA_VERSION, "label": LABEL,
           "pack": {"id": pack.id, "version": pack.version, **pack.hashes()},
           "background": {"file": Path(args.background).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
                          "weekly_volume": bg["weekly_volume"], "sites": bg["sites"]["count"],
                          "entities": bg["entities"]["count"]},
           "settings": {"rates": rates, "years": years, "seeds": seeds, "plants": args.plants,
                        "sextuplings": args.sextuplings, "ramp_weeks": args.ramp_weeks,
                        "sextuple_weeks": args.sextuple_weeks, "stagger_weeks": args.stagger_weeks,
                        "lookback_weeks": lookbacks, "threshold": args.threshold,
                        "max_control_share": args.max_control_share, "tie_salt": args.tie_salt},
           "summary": summary, "worlds": worlds}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "power.json", doc)
    (out / "power.md").write_text(render(doc), encoding="utf-8")
    gate = summary["gate"]
    failing = "; ".join(f"rate {f['rate']:g} over {f['years']} year{'' if f['years'] == 1 else 's'}: best "
                        f"{f['best'] or 'none'} "
                        f"{'n/a' if f['best_power'] is None else format(f['best_power'], '.2f')}"
                        for f in gate["failing"])
    print(f"power check: {'passes' if gate['passes'] else 'fails'} at look-back {gate['lookback_weeks']} weeks"
          f"{'' if gate['passes'] else ' (' + failing + ')'} -> {out / 'power.md'}")
    return 0 if gate["passes"] else 1


if __name__ == "__main__":
    sys.exit(main())

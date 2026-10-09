#!/usr/bin/env python
"""The null candidate rate of a pack's detectors: how often a series becomes a detector candidate in a synthetic world
with nothing planted, at a given number of sites.

    python tools/market/vehicle_null_rate.py --pack docs/collective/replay/vehicles/pack-v2 --sites 60
        [--weeks 106] [--seeds 1 2 3] [--vehicles N] [--detectors FILE] [--set burst.min_sites=3 ...] [--grid FILE]
        [--out FILE]

**The world** is the pack generator's (``packs.generator.generate``) with the first ``--sites`` sites and no plant:
synthetic records only. It runs through the pilot audit's pipeline (``evaluate.baselines``): each site ingests and
extracts lexically and emits k-suppressed weekly cells, and HQ runs X and S over them; model-free R runs over the
record-level fields as the audit runs it. 106 weeks is the replays' span (2023-01-01 to 2024-12-31 in ISO weeks).
``--vehicles N`` makes a sparser world: the generator draws from N fictional vehicles (:func:`fictional_vehicles`)
instead of its own, with the same record volume, so each series has fewer records.

**The rate.** The evaluated weeks are the audit's: from week ``window_weeks + min_history_weeks - 1`` on. Each series
(entity, predicate) with a cell in a channel's run is tested once per evaluated week. The null candidate rate is the
share of those tests at which the series is a detector candidate (D2 or D3, after G4's stale filter; a rule hit is not
a candidate). The share of series that are a candidate at least once is reported beside it, and both again over the
busiest quarter of the series (most records), where noise candidates gather. With no signal planted, every candidate
is a false one.

**The bar** (:func:`passes`): at most 5% in every channel, over all series and over the busiest quarter of them
(most records), pooled over the seeds. :func:`first_passing` picks the first setting of a ladder, least strict first,
that passes.

**Settings.** ``--detectors`` (a ``detectors.json``) and ``--set path=value`` (dotted, JSON values) replace the
pack's detector settings; ``--grid`` names several settings, each a set of changes to those. Each seed's world and
cells are built once, and every setting is measured on them, so two settings differ only in their thresholds. The
printed JSON gives each setting's rates per channel and seed, and pooled over the seeds (candidate steps over tests).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vehicle_pack import ENTITY  # noqa: E402
from vehicle_pack_v2 import set_dotted as with_settings  # noqa: E402
from mycelic.collective.evaluate.baselines import exact_result, r_mf_cells, run_pipeline, world_weeks  # noqa: E402
from mycelic.collective.jsonio import canonical_bytes, sha256_hex  # noqa: E402
from mycelic.collective.packs.generator import generate  # noqa: E402
from mycelic.collective.packs.loader import FrozenPack, load_pack_dir  # noqa: E402
from mycelic.collective.detect.detectors import run_detection  # noqa: E402

CHANNELS = ("X", "S", "R_mf")
TIE_SALT = "pilot"
REPLAY_WEEKS = 106
LIMIT = 0.05                                    # the handoff's bar for the null candidate rate at 60 sites
# make-model pairs of the sparser world; the generator's own six vehicles are among the first 60 ids
SPARSE_MODELS = ("ACME-ROADSTER", "ACME-HAULER", "BOLT-COMPACT", "BOLT-WAGON", "ZEPHYR-VAN", "ZEPHYR-COUPE")
SPARSE_FIRST_YEAR = 2015
MAX_VEHICLES = 600


def parse_set(items: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        path, sep, raw = item.partition("=")
        if not sep or not path:
            raise ValueError(f"--set takes path=value: {item!r}")
        out[path] = json.loads(raw)
    return out


def fictional_vehicles(n: int) -> list[str]:
    """``n`` fictional vehicle ids: the make-model pairs of :data:`SPARSE_MODELS` over model years from
    :data:`SPARSE_FIRST_YEAR` on, year by year."""
    if not 1 <= n <= MAX_VEHICLES:
        raise ValueError(f"--vehicles takes 1 to {MAX_VEHICLES}: {n}")
    years = range(SPARSE_FIRST_YEAR, SPARSE_FIRST_YEAR + -(-n // len(SPARSE_MODELS)))
    return [f"{m}-{y}" for y in years for m in SPARSE_MODELS][:n]


def with_vehicles(pack_dir: Path, vehicles: Sequence[str], workdir: Path) -> Path:
    """A copy of the pack whose generator draws from ``vehicles`` (every site's master data is "all" of them)."""
    d = workdir / "world-pack"
    shutil.copytree(pack_dir, d)
    path = d / "generator.json"
    gen = json.loads(path.read_text(encoding="utf-8"))
    gen["universe"] = {**gen["universe"], ENTITY: list(vehicles)}
    path.write_text(json.dumps(gen, indent=2) + "\n", encoding="utf-8")
    return d


def variant(pack_dir: Path, detectors: Mapping[str, Any], workdir: Path) -> FrozenPack:
    """The pack with ``detectors`` as its ``detectors.json``, loaded (so the loader checks the settings)."""
    d = workdir / ("pack-" + sha256_hex(canonical_bytes(dict(detectors)))[:12])
    if not d.exists():
        shutil.copytree(pack_dir, d)
        (d / "detectors.json").write_text(json.dumps(detectors, indent=2) + "\n", encoding="utf-8")
    return load_pack_dir(d)


def evaluated_weeks(pack: FrozenPack, weeks: Sequence[str]) -> list[str]:
    d = pack.detectors
    return list(weeks[d["window_weeks"] + d["min_history_weeks"] - 1:])


def _part(steps: Mapping[str, int], series: Sequence[str], weeks: int) -> dict[str, Any]:
    tests = len(series) * weeks
    n = sum(steps.get(k, 0) for k in series)
    ever = sum(1 for k in series if steps.get(k, 0))
    return {"series": len(series), "tests": tests, "candidate_steps": n, "rate": n / tests if tests else None,
            "series_ever": ever, "series_ever_share": ever / len(series) if series else None}


def rate(result: Mapping[str, Any], totals: Mapping[str, int], evaluated: Sequence[str]) -> dict[str, Any]:
    """Candidate steps over tests (series with a cell x evaluated weeks) and the series ever a candidate, over every
    series and over the busiest quarter of them (most records: the cells' lower-bound counts, ties by key)."""
    weeks = set(evaluated)
    steps = {c["key"]: sum(1 for w in c["candidate_weeks"] if w in weeks) for c in result["candidates"]}
    busy = sorted(totals, key=lambda k: (-totals[k], k))[:max(1, len(totals) // 4)] if totals else []
    return {"evaluated_weeks": len(weeks), **_part(steps, sorted(totals), len(weeks)),
            "busiest_quarter": _part(steps, busy, len(weeks))}


def _key(entity_type: str, entity_id: str, predicate: str) -> str:
    return f"{entity_type}:{entity_id}:{predicate}"


def world_inputs(pack: FrozenPack, seed: int, sites: int, weeks: int, workdir: Path) -> dict[str, Any]:
    """One seed's null world as the detectors see it: X and S bundles and cells from HQ's store, and model-free R's
    exact cells, with the org, as_of and weeks."""
    world = generate(pack, seed, sites, weeks)
    site_ids = list(world.params["site_ids"])
    wk = world_weeks(world.params["start"], weeks)
    pipeline = run_pipeline(pack, world.records, site_ids=site_ids, master_data=world.master_data, weeks=wk,
                            workdir=workdir)
    try:
        hq = {ch: pipeline.store.detection_inputs(pipeline.as_of, ch) for ch in ("X", "S")}
        r_mf = r_mf_cells(pack, world.records, master_data=world.master_data, last_week=wk[-1])
        out = {"org": pipeline.org, "as_of": pipeline.as_of, "weeks": wk, "hq": hq, "r_mf": r_mf,
               "records": len(world.records), "sites": len(site_ids)}
    finally:
        pipeline.close()
    return out


def measure(inputs: Mapping[str, Any], pack: FrozenPack) -> dict[str, Any]:
    """The rate per channel of ``pack``'s detectors over one world's inputs."""
    evaluated = evaluated_weeks(pack, inputs["weeks"])
    out = {}
    for ch in ("X", "S"):
        bundles, cells = inputs["hq"][ch]
        result = run_detection(pack, inputs["org"], bundles=bundles, cells=cells, as_of=inputs["as_of"],
                               run_channel=ch, tie_salt=TIE_SALT)
        totals: dict[str, int] = {}
        for c in cells:
            key = _key(c.entity_type, c.entity_id, c.predicate)
            totals[key] = totals.get(key, 0) + (1 if c.n is None else c.n)        # '<k' counts its lower bound
        out[ch] = rate(result, totals, evaluated)
    result = exact_result(pack, inputs["org"], inputs["r_mf"], as_of=inputs["as_of"], run_channel="S",
                          tie_salt=TIE_SALT)
    totals = {}
    for cells in inputs["r_mf"].values():
        for c in cells:
            key = _key(c["entity_type"], c["entity_id"], c["predicate"])
            totals[key] = totals.get(key, 0) + c["n"]
    out["R_mf"] = rate(result, totals, evaluated)
    return out


def pooled(per_seed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Each channel's counts summed over seeds; ``max_rate`` and ``max_series_ever_share`` over the channels."""
    out: dict[str, Any] = {}
    for ch in CHANNELS:
        out[ch] = {}
        for part, get in (("all", lambda s: s), ("busiest_quarter", lambda s: s["busiest_quarter"])):
            n = {f: sum(get(s[ch])[f] for s in per_seed) for f in ("tests", "candidate_steps", "series",
                                                                 "series_ever")}
            out[ch][part] = {**n, "rate": n["candidate_steps"] / n["tests"] if n["tests"] else None,
                             "series_ever_share": n["series_ever"] / n["series"] if n["series"] else None}
    for f in ("rate", "series_ever_share"):
        out[f"max_{f}"] = max(out[ch]["all"][f] for ch in CHANNELS if out[ch]["all"][f] is not None)
    return out


def passes(pooled_setting: Mapping[str, Any], limit: float = LIMIT) -> bool:
    """Every channel's rate is at most ``limit``, over all series and over the busiest quarter."""
    return all(pooled_setting[ch][part]["rate"] is not None and pooled_setting[ch][part]["rate"] <= limit
               for ch in CHANNELS for part in ("all", "busiest_quarter"))


def first_passing(doc: Mapping[str, Any], ladder: Sequence[str], limit: float = LIMIT) -> str | None:
    """The first setting of ``ladder`` (least strict first) whose pooled rates pass, or None."""
    return next((name for name in ladder if passes(doc["settings"][name]["pooled"], limit)), None)


def run(pack_dir: Path, settings: Mapping[str, Mapping[str, Any]], *, sites: int, weeks: int,
        seeds: Sequence[int], vehicles: Sequence[str] | None = None) -> dict[str, Any]:
    """Each named detector setting's rates on the same seeds' worlds; with ``vehicles``, worlds drawn from those
    vehicles instead of the generator's own."""
    base = load_pack_dir(pack_dir)
    per: dict[str, list[dict[str, Any]]] = {name: [] for name in settings}
    worlds = []
    with tempfile.TemporaryDirectory(prefix="mycelic-null-") as tmp:
        world_dir = with_vehicles(pack_dir, vehicles, Path(tmp)) if vehicles else pack_dir
        world_pack = load_pack_dir(world_dir)
        packs = {name: variant(world_dir, d, Path(tmp)) for name, d in settings.items()}
        for seed in seeds:
            inputs = world_inputs(world_pack, seed, sites, weeks, Path(tmp) / f"world-{seed}")
            worlds.append({"seed": seed, "records": inputs["records"], "sites": inputs["sites"]})
            for name in settings:
                per[name].append({"seed": seed, **measure(inputs, packs[name])})
        hashes = {name: packs[name].detector_hash for name in settings}
    doc = {"kind": "null_candidate_rate", "label": "synthetic null world: generated records, nothing planted",
           "pack": {"id": base.id, "version": base.version, **base.hashes()}, "sites": sites, "weeks": weeks,
           "seeds": list(seeds), "worlds": worlds,
           "settings": {name: {"detectors": dict(settings[name]), "detector_hash": hashes[name],
                               "per_seed": per[name], "pooled": pooled(per[name])} for name in settings}}
    if vehicles:
        doc["vehicles"] = list(vehicles)
    return doc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--pack", required=True, help="a pack directory whose generator has at least --sites sites")
    p.add_argument("--sites", type=int, required=True)
    p.add_argument("--weeks", type=int, default=REPLAY_WEEKS)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--vehicles", type=int, help="draw the world from this many fictional vehicles instead of the "
                                                "generator's own (a sparser world)")
    p.add_argument("--detectors", help="a detectors.json to use instead of the pack's")
    p.add_argument("--set", nargs="*", default=[], help="dotted detector setting=JSON value, applied last")
    p.add_argument("--grid", help="a JSON file {name: {dotted setting: value}}: each name is a setting measured on "
                                  "the same worlds (the detectors above with those changes)")
    p.add_argument("--out", help="also write the JSON here")
    args = p.parse_args(argv)
    pack_dir = Path(args.pack)
    detectors = json.loads(Path(args.detectors or pack_dir / "detectors.json").read_text(encoding="utf-8"))
    try:
        detectors = with_settings(detectors, parse_set(args.set))
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    try:
        grid = json.loads(Path(args.grid).read_text(encoding="utf-8")) if args.grid else {"run": {}}
        settings = {name: with_settings(detectors, changes) for name, changes in grid.items()}
        vehicles = fictional_vehicles(args.vehicles) if args.vehicles is not None else None
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    doc = run(pack_dir, settings, sites=args.sites, weeks=args.weeks, seeds=args.seeds, vehicles=vehicles)
    text = json.dumps(doc, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

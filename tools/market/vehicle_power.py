#!/usr/bin/env python
"""Write the vehicle-like background for the power check (``mycelic.collective.pilot.power``), from numbers the
repository already holds.

    python tools/market/vehicle_power.py --probe docs/collective/replay/vehicles/nhtsa-probe.json
        --pack docs/collective/replay/vehicles/pack --out docs/collective/power/vehicle-background.json

Every number is either transcribed from a recorded run, with its source, or an assumption, named as one:

* **Volume and sites** (``CHOICE-V001.md``, run 002): 27,397 complaints on one make received in the 731 days from
  2023-01-01 to 2024-12-31, from 60 sites (states).
* **Entities** (``DIAG-E001.md``, run 2): 332,225 complaints on 3,420 vehicles in 2015-2024 give the mean complaints a
  week per vehicle; the make's volume over that mean gives the number of vehicles.
* **Failure shares** (``nhtsa-probe.json``, ``top_components``): complaints per top-level component category in
  2015-2024; a category the pack does not name counts as unknown or other, as in the exporter.
* **Plant bands** (``DIAG-E001.md``, run 2): a ramp goes on a vehicle as busy as the events' vehicles, whose matching
  complaints over their matching share give 4.8 to 8.75 complaints a quarter (investigations at quarters -12 and -1;
  recalls lie inside). A sextupling goes on a vehicle and failure whose own rate is that of the recalls' matching
  complaints, 0.97 to 2.27 a quarter over quarters -12 to -1.
* **Assumptions:** vehicle rates follow a gamma with shape 0.5 (the run gives only the mean); site weights fall as
  1 / rank (no state shares are recorded); rates do not change over time; each complaint has one component.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

GENERIC_CATEGORY = "UNKNOWN OR OTHER"
V001_COMPLAINTS, V001_DAYS, V001_SITES = 27397, 731, 60
DIAG_COMPLAINTS, DIAG_VEHICLES, DIAG_DAYS = 332225, 3420, 3653          # 2015-01-01 to 2024-12-31
EVENT_VEHICLE_PER_QUARTER = (0.24 / 0.05, 1.75 / 0.20)                  # investigations, quarters -12 and -1
RECALL_MATCHING_PER_QUARTER = (0.97, 2.27)                              # recalls, quarters -12 to -1
WEEKS_PER_QUARTER = 365.25 / 4 / 7
GAMMA_SHAPE = 0.5
SITE_SKEW = 1.0
START = "2021-01-04"
SITE_FORMAT = "s{n:02d}"
VEHICLE_FORMAT = "SYN-V{n:04d}-2020"


def weights(probe: Mapping[str, Any], mapping: Mapping[str, Any], codes: Mapping[str, Any]) -> dict[str, int]:
    """Complaints per pack predicate from the probe's component counts; other categories count as unknown or other."""
    value_map = mapping["codes"][0]["value_map"]
    generic = codes[value_map[GENERIC_CATEGORY]]["predicate"]
    out: dict[str, int] = {}
    for category, count in probe["complaints"]["top_components"]:
        code = value_map.get(category)
        pred = codes[code]["predicate"] if code is not None else generic
        out[pred] = out.get(pred, 0) + int(count)
    return dict(sorted(out.items()))


def background(probe: Mapping[str, Any], pack_dir: Path) -> dict[str, Any]:
    mapping = json.loads((pack_dir / "mapping.json").read_text(encoding="utf-8"))
    codes = json.loads((pack_dir / "codes.json").read_text(encoding="utf-8"))
    volume = V001_COMPLAINTS * 7 / V001_DAYS
    per_vehicle = DIAG_COMPLAINTS / DIAG_VEHICLES / (DIAG_DAYS / 7)
    vehicles = round(volume / per_vehicle)
    lo, hi = (v / WEEKS_PER_QUARTER for v in EVENT_VEHICLE_PER_QUARTER)
    klo, khi = (v / WEEKS_PER_QUARTER for v in RECALL_MATCHING_PER_QUARTER)
    return {
        "kind": "power_background", "schema_version": 1,
        "label": ("synthetic vehicle-like background: volumes from V001 and DIAG-E001, failure shares from the "
                  "probe; vehicle rates, site weights and flat time are assumptions"),
        "start": START, "weekly_volume": round(volume, 2),
        "sites": {"count": V001_SITES, "skew": SITE_SKEW, "id_format": SITE_FORMAT},
        "entities": {"type": mapping["primary_entity_type"], "count": vehicles, "gamma_shape": GAMMA_SHAPE,
                     "id_format": VEHICLE_FORMAT},
        "predicates": weights(probe, mapping, codes),
        "plant_entity_rate": [round(lo, 3), round(hi, 3)],
        "sextuple_key_rate": [round(klo, 3), round(khi, 3)],
        "sources": {
            "weekly_volume": f"CHOICE-V001.md run 002: {V001_COMPLAINTS} complaints in {V001_DAYS} days",
            "sites": f"CHOICE-V001.md run 001: {V001_SITES} sites; skew {SITE_SKEW:g} is an assumption",
            "entities": (f"DIAG-E001.md run 2: {DIAG_COMPLAINTS} complaints on {DIAG_VEHICLES} vehicles in "
                         f"{DIAG_DAYS} days, {per_vehicle:.4f} a week per vehicle; gamma shape {GAMMA_SHAPE:g} is "
                         "an assumption"),
            "predicates": "nhtsa-probe.json top_components (2015-2024, all makes); other categories as unknown",
            "plant_entity_rate": ("DIAG-E001.md run 2: investigations' matching complaints over matching share, "
                                  "0.24/0.05 and 1.75/0.20 a quarter"),
            "sextuple_key_rate": "DIAG-E001.md run 2: recalls' matching complaints, 0.97 to 2.27 a quarter",
            "time": "rates are flat over time (an assumption); one component per complaint (an assumption)"}}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--probe", required=True)
    p.add_argument("--pack", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    probe = json.loads(Path(args.probe).read_text(encoding="utf-8"))
    doc = background(probe, Path(args.pack))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"vehicle background: {doc['weekly_volume']} complaints a week, {doc['sites']['count']} sites, "
          f"{doc['entities']['count']} vehicles -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

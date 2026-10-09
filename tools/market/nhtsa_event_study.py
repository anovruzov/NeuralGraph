#!/usr/bin/env python
"""Diagnostic E001: when do complaints about a recalled component rise, relative to the recall or investigation?

    python tools/market/nhtsa_event_study.py --out event-study.json

Exploratory and descriptive, with no pass or fail (`docs/collective/replay/vehicles/DIAG-E001.md`). For the six makes of
the vehicle replays it reads NHTSA's complaint files received 2015-2024, the recall file and the investigation file,
and for every event dated 2018-01-01 to 2023-12-31 (a recall campaign on a vehicle, or a preliminary evaluation or
defect petition on a vehicle) it counts the vehicle's complaints by quarter relative to the event, from 12 quarters
before to 4 after. A vehicle is make-model-year (``vehicle_pack.vehicle_id``), as in the replays; a complaint matches
an event's component when one of its top-level component categories equals one the event names.

It prints aggregates only (no complaint, recall or investigation content): per event kind, for each relative quarter,
the events' mean complaints on the vehicle, the mean matching-component complaints, and the matching share; how many
events have any complaint at all on their vehicle (a naming check between the files); and where each event's
matching complaints from the 3 years before fall in time (inside the replays' 26-week look-back or earlier; and, for
events dated 2023, the replays' own window, how many came before 2023, where the replays' export started).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nhtsa_export import (  # noqa: E402
    BASE, C_COMP, C_LDATE, C_MAKE, C_MODEL, C_ODINO, C_YEAR, I_ACTION, I_COMP, I_MAKE, I_MODEL, I_ODATE, I_YEAR,
    INVESTIGATION_KINDS, INVESTIGATIONS_URL, R_CAMPNO, R_COMP, R_MAKE, R_MODEL, R_RCDATE, R_TYPE, R_YEAR, RECALLS_URL,
    _fetch, _field, _rows)
from vehicle_pack import category_of, vehicle_id  # noqa: E402

MAKES = ("FORD", "CHEVROLET", "JEEP", "HONDA", "NISSAN", "DODGE")
COMPLAINT_URLS = (f"{BASE}/cmpl/COMPLAINTS_RECEIVED_2015-2019.zip", f"{BASE}/cmpl/COMPLAINTS_RECEIVED_2020-2024.zip")
EVENTS_FROM, EVENTS_TO = date(2018, 1, 1), date(2023, 12, 31)
QUARTERS_BEFORE, QUARTERS_AFTER = 12, 4
LOOKBACK_DAYS = 26 * 7
EXPORT_START = date(2023, 1, 1)


def _day(text: str) -> date | None:
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def complaint_index(rows: Iterable[Sequence[str]], makes: Sequence[str]) -> dict[str, dict[str, tuple[date, set[str]]]]:
    """vehicle -> ODINO -> (received date, top-level categories), for the makes' vehicle complaints."""
    index: dict[str, dict[str, tuple[date, set[str]]]] = defaultdict(dict)
    wanted = set(makes)
    for row in rows:
        make = _field(row, C_MAKE).upper()
        if make not in wanted:
            continue
        day = _day(_field(row, C_LDATE))
        vid = vehicle_id(make, _field(row, C_MODEL), _field(row, C_YEAR))
        if day is None or vid is None:
            continue
        odino = _field(row, C_ODINO)
        entry = index[vid].get(odino)
        if entry is None:
            index[vid][odino] = (day, {category_of(_field(row, C_COMP))})
        else:
            entry[1].add(category_of(_field(row, C_COMP)))
    return index


def events(rows: Iterable[Sequence[str]], kind: str, makes: Sequence[str]) -> list[dict[str, Any]]:
    """One event per (campaign or action, vehicle) dated in [EVENTS_FROM, EVENTS_TO]: its date and categories."""
    wanted = set(makes)
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if kind == "recall":
            make, model, year = _field(row, R_MAKE).upper(), _field(row, R_MODEL), _field(row, R_YEAR)
            if _field(row, R_TYPE) != "V":
                continue
            ident, day, comp = _field(row, R_CAMPNO), _day(_field(row, R_RCDATE)), _field(row, R_COMP)
        else:
            make, model, year = _field(row, I_MAKE).upper(), _field(row, I_MODEL), _field(row, I_YEAR)
            ident = _field(row, I_ACTION).upper()
            if ident[:2] not in INVESTIGATION_KINDS:
                continue
            day, comp = _day(_field(row, I_ODATE)), _field(row, I_COMP)
        if make not in wanted or day is None or not EVENTS_FROM <= day <= EVENTS_TO:
            continue
        vid = vehicle_id(make, model, year)
        if vid is None:
            continue
        entry = by.setdefault((ident, vid), {"make": make, "vehicle": vid, "date": day, "categories": set()})
        entry["date"] = min(entry["date"], day)
        entry["categories"].add(category_of(comp))
    return sorted(by.values(), key=lambda e: (e["date"], e["vehicle"]))


def quarter_of(delta_days: int) -> int:
    """The relative quarter of a day offset: -1 is the 91 days before the event, 0 the 91 days from it."""
    return delta_days // 91


def study(evs: Sequence[Mapping[str, Any]],
          index: Mapping[str, Mapping[str, tuple[date, set[str]]]]) -> dict[str, Any]:
    quarters = list(range(-QUARTERS_BEFORE, QUARTERS_AFTER))
    totals = {q: [0, 0] for q in quarters}          # all complaints, matching complaints, summed over events
    with_any = with_matching_before = 0
    placement = {"in_lookback": 0, "earlier_in_3y": 0, "before_export_start": 0, "matching_3y_total": 0}
    events_with_lookback_mass = events_mostly_before_export = 0
    # the replays' own window: events dated in or after the export start, where "before 2023" means unseen history
    replay = {"events": 0, "events_with_matching_before": 0, "in_lookback": 0, "earlier_in_3y": 0,
              "before_export_start": 0, "events_mostly_before_export": 0}
    for ev in evs:
        complaints = index.get(ev["vehicle"], {})
        if complaints:
            with_any += 1
        in_lb = early = pre_export = 0
        for day, cats in complaints.values():
            delta = (day - ev["date"]).days
            q = quarter_of(delta)
            matching = bool(cats & ev["categories"])
            if q in totals:
                totals[q][0] += 1
                totals[q][1] += int(matching)
            if matching and -QUARTERS_BEFORE * 91 <= delta < 0:
                placement["matching_3y_total"] += 1
                if delta >= -LOOKBACK_DAYS:
                    in_lb += 1
                else:
                    early += 1
                if day < EXPORT_START:
                    pre_export += 1
        placement["in_lookback"] += in_lb
        placement["earlier_in_3y"] += early
        placement["before_export_start"] += pre_export
        if in_lb + early:
            with_matching_before += 1
            events_with_lookback_mass += int(in_lb > early)
            events_mostly_before_export += int(pre_export * 2 > in_lb + early)
        if ev["date"] >= EXPORT_START:
            replay["events"] += 1
            replay["in_lookback"] += in_lb
            replay["earlier_in_3y"] += early
            replay["before_export_start"] += pre_export
            if in_lb + early:
                replay["events_with_matching_before"] += 1
                replay["events_mostly_before_export"] += int(pre_export * 2 > in_lb + early)
    n = len(evs)
    curve = [{"quarter": q, "mean_complaints": round(totals[q][0] / n, 3) if n else None,
              "mean_matching": round(totals[q][1] / n, 3) if n else None,
              "matching_share": round(totals[q][1] / totals[q][0], 4) if totals[q][0] else None} for q in quarters]
    return {"events": n, "vehicles": len({e["vehicle"] for e in evs}), "events_with_any_complaint": with_any,
            "events_with_matching_complaints_in_3y_before": with_matching_before,
            "events_whose_3y_matching_mass_is_mostly_in_the_26w_lookback": events_with_lookback_mass,
            "events_whose_3y_matching_mass_is_mostly_before_2023": events_mostly_before_export,
            "matching_complaints_3y_before": placement, "events_dated_from_2023": replay, "by_quarter": curve,
            "by_make": {m: sum(1 for e in evs if e["make"] == m) for m in MAKES}}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    index: dict[str, dict[str, tuple[date, set[str]]]] = defaultdict(dict)
    sizes = {}
    for url in COMPLAINT_URLS:
        blob = _fetch(url)
        sizes[url.rsplit("/", 1)[1]] = len(blob)
        for vid, complaints in complaint_index(_rows(blob), MAKES).items():
            for odino, entry in complaints.items():
                old = index[vid].get(odino)
                index[vid][odino] = entry if old is None else (min(old[0], entry[0]), old[1] | entry[1])
    result: dict[str, Any] = {"kind": "nhtsa_event_study", "diagnostic": "E001", "makes": list(MAKES),
                              "events_window": [EVENTS_FROM.isoformat(), EVENTS_TO.isoformat()],
                              "quarters": [-QUARTERS_BEFORE, QUARTERS_AFTER - 1], "complaint_files": sizes,
                              "complaints_indexed": sum(len(v) for v in index.values()), "vehicles_indexed": len(index)}
    for kind, url in (("recall", RECALLS_URL), ("investigation", INVESTIGATIONS_URL)):
        evs = events(_rows(_fetch(url)), kind, MAKES)
        result[kind] = study(evs, index)
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("=== EVENT-STUDY BEGIN ===")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    print("=== EVENT-STUDY END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Re-score one pilot audit from its ``audit.json`` alone: each channel's found count and both chance nulls, and the
same for the union of the channels.

    python tools/market/rescore_audit.py audit/audit.json

The audit keeps what its scoring reads: per channel the outcomes it scored (``by_outcome``: id, key, opened) and every
alert (``alert_timeline``: available date, week and keys), the look-back and post windows (``settings``), the
evaluated weeks and the date the rotations start from (``weeks.available_first``). This tool rebuilds the outcomes and
alerts from those and runs the audit's own ``score_channel`` on them, with no export, pack or pipeline. Each channel's
result is printed beside ``matches_audit``: whether it equals the audit's own summary, key for key.

The **union** counts an outcome found when any channel found it. Its alerts are all the channels' alerts together
(an alert in two channels counts twice in ``alerts``), and both of its nulls rotate that combined timeline. A channel
that did not run is left out of it.

An audit written before ``weeks.available_first`` was kept takes the date from its alert timeline: each alert is
available on its week's Sunday plus the pack's closing lag, the same lag for every alert, and the rotations start at
the first evaluated week's closing date. With no alert at all, the date changes nothing.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mycelic.collective.edge.weeks import week_sunday  # noqa: E402
from mycelic.collective.pilot.audit import CHANNELS, Outcome, _match_keys, score_channel  # noqa: E402

KIND = "pilot_audit_rescore"


class RescoreError(ValueError):
    pass


def start_date(doc: Mapping[str, Any]) -> tuple[date, str]:
    """The date the rotations start from, and where it came from (``audit`` or ``alert_timeline``)."""
    weeks = doc["weeks"]
    if weeks.get("available_first"):
        return date.fromisoformat(weeks["available_first"]), "audit"
    lags = {(date.fromisoformat(t["available_date"]) - week_sunday(t["week"])).days
            for c in doc["channels"].values() for t in c.get("alert_timeline") or []}
    if len(lags) > 1:
        raise RescoreError(f"the alert timeline has {len(lags)} different closing lags; it cannot be one audit's")
    return week_sunday(weeks["evaluated_from"]) + timedelta(days=lags.pop() if lags else 0), "alert_timeline"


def scored_outcomes(doc: Mapping[str, Any]) -> list[Outcome]:
    """The outcomes every channel scored. The key is kept whole, so it reads back exactly as the audit wrote it."""
    lists = [[(r["outcome_id"], r["key"], r["opened"]) for r in doc["channels"][c]["by_outcome"]]
             for c in CHANNELS if doc["channels"][c].get("summary") is not None]
    if not lists:
        raise RescoreError("no channel of this audit was scored")
    if any(rows != lists[0] for rows in lists[1:]):
        raise RescoreError("the channels list different outcomes; this is not one audit's file")
    out = []
    for oid, key, opened in lists[0]:
        entity_type, entity_id = key.split(":", 1)
        out.append(Outcome(oid, opened, entity_type, entity_id, None))
    return out


def timeline_alerts(name: str, timeline: Any) -> list[dict[str, Any]]:
    """Alerts as ``score_channel`` reads them, from one channel's timeline. The rank only breaks ties between alerts
    available on the same day in the same week, which changes no summary number, so it is left at zero."""
    if timeline is None:
        raise RescoreError(f"channel {name} has no alert_timeline: the audit was written before it was kept; re-run it")
    alerts = []
    for t in timeline:
        full = max(t["keys"], key=len)
        entity_type, entity_id, predicate = full.split(":", 2)
        a = {"available_date": t["available_date"], "week": t["week"], "rank": 0, "entity_type": entity_type,
             "entity_id": entity_id, "predicate": predicate}
        if sorted(_match_keys(a)) != sorted(t["keys"]):
            raise RescoreError(f"channel {name}: the keys {t['keys']} are not one alert's")
        alerts.append(a)
    return alerts


def rescore(doc: Mapping[str, Any]) -> dict[str, Any]:
    first, source = start_date(doc)
    settings = {"lookback": doc["settings"]["lookback_weeks"], "post": doc["settings"]["post_weeks"],
                "available_first": first, "evaluated_weeks": doc["weeks"]["evaluated_weeks"]}
    outcomes = scored_outcomes(doc)
    channels: dict[str, Any] = {}
    union: list[dict[str, Any]] = []
    union_of = []
    for name in CHANNELS:
        ch = doc["channels"][name]
        if ch.get("summary") is None:
            channels[name] = {"summary": None, "reason": ch.get("reason")}
            continue
        alerts = timeline_alerts(name, ch.get("alert_timeline"))
        union += alerts
        union_of.append(name)
        summary = score_channel(outcomes, alerts, **settings)["summary"]
        channels[name] = {"summary": summary, "matches_audit": summary == ch["summary"]}
    return {"kind": KIND, "pack": doc["pack"]["id"], "label": doc["label"],
            "available_first": first.isoformat(), "available_first_from": source,
            "settings": {"lookback_weeks": settings["lookback"], "post_weeks": settings["post"],
                         "evaluated_weeks": settings["evaluated_weeks"]},
            "channels": channels,
            "union": {"of": union_of, "summary": score_channel(outcomes, union, **settings)["summary"]}}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: rescore_audit.py <audit.json>", file=sys.stderr)
        return 2
    try:
        doc = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        result = rescore(doc)
    except (OSError, ValueError, KeyError) as exc:
        print(f"rescore_audit: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

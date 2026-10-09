#!/usr/bin/env python
"""Public replay V002's summary: one pilot audit per make, combined by the rule fixed before the run.

    python tools/market/vehicle_summary.py vaudit

Reads ``<dir>/<MAKE>/audit.json`` for every make directory and prints one JSON object: per make and channel the
alerts, found, in-scope outcomes, expected-by-chance and p; per channel the sums over makes and Fisher's combined p of
the makes' circular-shift p-values. `docs/collective/replay/vehicles/CHOICE-V002.md` fixes how it is read: a channel
shows early warning beyond chance on this set only if its combined p is below ``ALPHA`` (0.05 over three channels).
Each make's p counts the identity shift among its shifts, so it is never 0 and the combination is conservative.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

CHANNELS = ("X", "S", "R_mf")
ALPHA = 0.05 / len(CHANNELS)


def fisher(p_values: Sequence[float]) -> float:
    """Fisher's combined p: P(chi-squared with 2k degrees of freedom >= -2 sum ln p), in closed form for even df."""
    if not p_values:
        raise ValueError("no p-values")
    half = -sum(math.log(p) for p in p_values)          # the statistic over two
    term, total = 1.0, 1.0
    for j in range(1, len(p_values)):
        term *= half / j
        total += term
    return min(1.0, math.exp(-half) * total)


def summarise(audits: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    makes = {}
    for make, doc in sorted(audits.items()):
        makes[make] = {
            "records": doc["export"]["records"], "sites": len(doc["export"]["sites"]),
            "codes_share": doc["export"]["coverage"]["codes"]["share"], "in_scope": doc["outcomes"]["in_scope"],
            "evaluated_weeks": doc["weeks"]["evaluated_weeks"],
            "channels": {c: {k: doc["channels"][c]["summary"][k]
                             for k in ("alerts", "found", "expected_found", "p_value", "unexplained_alerts")}
                         for c in CHANNELS}}
    channels = {}
    for c in CHANNELS:
        rows = [m["channels"][c] for m in makes.values()]
        scored = [r["p_value"] for r in rows if r["p_value"] is not None]
        combined = fisher(scored) if scored else None
        channels[c] = {"found": sum(r["found"] for r in rows),
                       "expected_found": round(sum(r["expected_found"] or 0.0 for r in rows), 2),
                       "alerts": sum(r["alerts"] for r in rows), "makes_scored": len(scored),
                       "makes_above_chance": sum(1 for r in rows if r["found"] > (r["expected_found"] or 0.0)),
                       "fisher_p": combined, "beyond_chance": combined is not None and combined < ALPHA}
    return {"kind": "vehicle_replay_summary", "alpha": ALPHA, "makes": makes, "channels": channels,
            "in_scope": sum(m["in_scope"] for m in makes.values())}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: vehicle_summary.py <audit dir with one directory per make>", file=sys.stderr)
        return 2
    root = Path(args[0])
    audits = {d.name: json.loads((d / "audit.json").read_text(encoding="utf-8"))
              for d in sorted(root.iterdir()) if (d / "audit.json").is_file()}
    if not audits:
        print(f"no <make>/audit.json under {root}", file=sys.stderr)
        return 1
    print(json.dumps(summarise(audits), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The parallelism proof, as arithmetic over measured intervals.

An interval is one agent's ``[start, end)`` from its own stamps; an open interval ends at ``now``.  The
interval graph has an edge where two intervals overlap; its largest clique omega(E) is the most work that was
genuinely in flight at one instant, and for intervals it is found exactly by a sweep.  Registered, queued,
running and complete are counted separately because they are different things, and the spec says so.

What this module does *not* know, and says so: model-request boundaries inside an agent (the stamps bound
the agent, not its calls), and queue latency unless an ``ENQUEUED`` stamp exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


def parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class Interval:
    agent: str
    start: float
    end: float | None
    kind: str = "agent"


def swarm_intervals(registry: Iterable[dict[str, Any]]) -> list[Interval]:
    out = []
    for a in registry:
        s = parse_ts(a.get("start"))
        if s is None:
            continue
        out.append(Interval(agent=a["id"], start=s, end=parse_ts(a.get("end")), kind="agent"))
    return out


def max_overlap(intervals: Iterable[Interval], now: float) -> tuple[int, float | None]:
    """omega(E) and the first instant it is reached.  Ends sort before starts at equal time, so touching
    intervals do not count as overlapping."""
    marks: list[tuple[float, int]] = []
    for iv in intervals:
        end = iv.end if iv.end is not None else now
        if end <= iv.start:
            continue
        marks.append((iv.start, +1))
        marks.append((end, -1))
    marks.sort()
    best, at, cur = 0, None, 0
    for t, d in marks:
        cur += d
        if cur > best:
            best, at = cur, t
    return best, at


def overlap_series(intervals: Iterable[Interval], now: float, points: int = 120) -> list[tuple[float, int]]:
    """``points`` samples of how many intervals were open, from the earliest start to ``now``."""
    ivs = list(intervals)
    if not ivs:
        return []
    t0 = min(iv.start for iv in ivs)
    t1 = max(now, max((iv.end or now) for iv in ivs))
    if t1 <= t0:
        return [(t0, len(ivs))]
    step = (t1 - t0) / max(1, points - 1)
    out = []
    for i in range(points):
        t = t0 + i * step
        k = sum(1 for iv in ivs if iv.start <= t < (iv.end if iv.end is not None else now))
        out.append((t, k))
    return out


def stats(registry: list[dict[str, Any]], now: float) -> dict[str, Any]:
    ivs = swarm_intervals(registry)
    omega, at = max_overlap(ivs, now)
    running = sum(1 for a in registry if a.get("state") == "RUNNING")
    complete = [a for a in registry if a.get("state") == "COMPLETE"]
    durations = [parse_ts(a["end"]) - parse_ts(a["start"]) for a in complete
                 if parse_ts(a.get("end")) is not None and parse_ts(a.get("start")) is not None]
    first = min((iv.start for iv in ivs), default=None)
    elapsed = (now - first) if first is not None else None
    return {
        "registered": len(registry),
        "queued": sum(1 for a in registry if a.get("state") == "QUEUED"),
        "running": running,
        "complete": len(complete),
        "failed": sum(1 for a in registry if a.get("state") == "FAILED"),
        "omega_now": sum(1 for iv in ivs if iv.end is None),
        "omega_peak": omega,
        "omega_peak_at": datetime.fromtimestamp(at, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if at else None,
        "throughput_per_min": (len(complete) / (elapsed / 60.0)) if elapsed else None,
        "mean_runtime_s": (sum(durations) / len(durations)) if durations else None,
        "queue_latency": None,          # no ENQUEUED stamp in this layer; reported as NO DATA, not invented
        "model_requests_in_flight": None,   # not instrumented at the builder layer; stamps bound the agent, not its calls
        "series": overlap_series(ivs, now),
        "intervals": [{"agent": iv.agent, "start": iv.start, "end": iv.end} for iv in ivs],
    }

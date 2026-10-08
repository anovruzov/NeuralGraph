"""Independent support from lineage roots (docs/mycelic/DECISIONS.md D11).

A claim is only as independent as the *original sources* behind it. Holders fingerprint each source
(``source_root_id``); several references that share a root are copies of one source and count once.
References whose root the holder could not determine (``root_known = 0``) are reported as *unknown
independence* and never counted as independent. This mirrors ``FragilityMetrics`` in the coordination
research track (apparent replica count vs. unique lineage roots) with production storage.

Only *active* references count. A reference whose source was revised, retracted or became unavailable is reported
(``inactive_refs``) but never counted as current support: the claim it backed waits for re-verification instead.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..util import parse_iso, utcnow


def is_active(ref: Mapping[str, Any]) -> bool:
    """A reference counts as current support only while its source is unchanged (candidates not yet stored have no status)."""
    return (ref.get("status") or "active") == "active"


def compute_support(refs: Iterable[Mapping[str, Any]], *, roles: Iterable[str] = ("supports",)) -> dict[str, Any]:
    """Summarize support for the given evidence references (rows of ``evidence_refs`` joined with ``claim_evidence``)."""
    wanted = set(roles)
    rows = [r for r in refs if (r.get("role") or "supports") in wanted]
    roots: dict[str, list[Mapping[str, Any]]] = {}
    unknown: list[str] = []
    contradicting: list[str] = []
    for r in refs:
        if (r.get("role") or "supports") == "contradicts":
            contradicting.append(r["ref_id"])
    inactive: list[str] = []
    for r in rows:
        if not is_active(r):
            inactive.append(r["ref_id"])
            continue
        if r.get("root_known") and r.get("source_root_id"):
            roots.setdefault(r["source_root_id"], []).append(r)
        else:
            unknown.append(r["ref_id"])
    independent = len(roots)
    copied = sum(len(v) - 1 for v in roots.values())
    holders = sorted({r["holder_id"] for r in rows if is_active(r)})
    root_list = [{"source_root_id": k, "ref_count": len(v), "holders": sorted({x["holder_id"] for x in v}),
                  "copied": len(v) > 1, "latest_observed_at": max((x.get("observed_at") or "") for x in v) or None}
                 for k, v in sorted(roots.items())]
    shared = [x for x in root_list if len(x["holders"]) > 1]
    return {
        "independent_roots": independent,
        "copied_refs": copied,
        "unknown_independence": len(unknown),
        "unknown_ref_ids": unknown,
        "holders": holders,
        "roots": root_list,
        "shared_dependencies": shared,        # one source held by several holders: known shared dependency
        "contradicting_refs": contradicting,
        "apparent_refs": len(rows),
        "inactive_refs": len(inactive),       # revised / retracted / unavailable at the source: shown, never counted
        "inactive_ref_ids": inactive,
    }


def evidence_time(ref: Mapping[str, Any]) -> Any:
    """When the content was last known to be true: the time it was observed (written, recorded, measured), or a later
    explicit re-confirmation by its holder (``meta.reconfirmed_at``). Never the ingestion or disclosure time: a roster
    written 200 days ago and uploaded today is 200 days old. ``freshness_at`` is only a fallback for references that carry
    no observation time."""
    meta = ref.get("meta") if isinstance(ref.get("meta"), Mapping) else {}
    stamps = [parse_iso(ref.get("observed_at")), parse_iso(meta.get("reconfirmed_at"))]
    stamps = [s for s in stamps if s is not None]
    if stamps:
        return max(stamps)
    return parse_iso(ref.get("freshness_at"))


def freshness(refs: Iterable[Mapping[str, Any]], *, freshness_days: float, now: Any | None = None) -> dict[str, Any]:
    """Newest evidence time across the references (:func:`evidence_time`) and whether the policy considers it stale."""
    now = now or utcnow()
    stamps = [evidence_time(r) for r in refs]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        return {"freshness_at": None, "age_days": None, "stale": False, "unknown": True}
    newest = max(stamps)
    age = (now - newest).total_seconds() / 86400.0
    return {"freshness_at": newest.isoformat(timespec="seconds"), "age_days": round(age, 1), "stale": age > freshness_days, "unknown": False}

"""Raw source writers: ``local_export`` JSONL files (header + records, edits, deletes, forwarded copies, missing metadata,
malformed lines) from the planner's :class:`world.Rec` list (PLAN_v1 §B.2).

``write_sources`` writes ``<out>/sources/<file>.jsonl`` (+ ``.late.jsonl`` for records that arrive in a second phase) and returns
the manifest the feeder uses. Raw records only: no derived field, no domain, no entity, no answer. The manifest carries no gold.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .faults import apply_file_faults
from .world import APPS, Ids, Rec, World

FORWARD_HEADER = "---------- Forwarded message ---------"


def stamp(now: datetime, days_ago: float, offset_days: float = 0.0) -> str:
    return (now - timedelta(days=days_ago) + timedelta(days=max(0.0, offset_days))).replace(microsecond=0).isoformat()


def app_for(holder_key: str) -> str:
    return APPS[int(hashlib.sha256(holder_key.encode()).hexdigest()[:4], 16) % len(APPS)]


def account_for(holder_key: str, source: str = "s0") -> str:
    return f"acct-{holder_key}-{source}"      # one connection per (app, account) in a holder: one account per source file


def forwarded_body(comment: str, original_text: str, sender: str, when: str, subject: str) -> str:
    return (f"{comment or 'FYI'}\n\n{FORWARD_HEADER}\nFrom: {sender}\nDate: {when}\nSubject: {subject}\n\n{original_text}")


def record_line(rec: Rec, by_rid: dict[str, Rec], now: datetime, world: World) -> dict[str, Any]:
    t = world.tenants[rec.tenant]
    base = {"id": rec.rid}
    if rec.typ == "delete":
        return {"type": "delete", "object_type": "document", **base, "deleted_at": stamp(now, rec.days_ago, rec.version_offset_days)}
    out: dict[str, Any] = {"type": rec.typ, "object_type": "document", **base, "title": rec.title, "text": rec.text}
    if rec.forwarded and rec.forward_of:
        orig = by_rid[rec.forward_of]
        when = stamp(now, orig.days_ago)
        out["text"] = forwarded_body(rec.comment, orig.text, "a colleague", when, orig.title)
        out["forwarded_from"] = {"app": app_for(orig.holder), "account": account_for(orig.holder, "s0"), "object_type": "document", "object_id": orig.rid}
    if not rec.missing_meta:
        out["created_at"] = stamp(now, rec.days_ago)
        out["author"] = rec.author
        if rec.version_offset_days:
            out["updated_at"] = stamp(now, rec.days_ago, rec.version_offset_days)
    return out


def source_header(holder_key: str, source: str, vis: str, domain: str | None, members: list[str]) -> dict[str, Any]:
    """``s0`` is the holder's main source (visibility = the holder's own, drawn from one distribution for every class), ``s1`` the
    owner's private notebook. Names and ids carry no role."""
    h: dict[str, Any] = {"type": "source", "id": f"{holder_key}-{source}", "name": "Notes" if source == "s0" else "Notebook", "source_type": "channel",
                         "visibility": vis}
    if vis != "public":
        h["member_ids"] = members
    if domain and source == "s0":
        h["domains"] = [domain]
    return h


def write_sources(world: World, records: list[Rec], fault_plan: dict[str, Any], ids: Ids, out_dir: Path, *, now: datetime, seed: int) -> list[dict[str, Any]]:
    """Write every source file; returns the manifest entries (one per file)."""
    src_dir = Path(out_dir) / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    by_rid = {r.rid: r for r in records if r.typ == "document"}
    groups: dict[tuple[str, str], list[Rec]] = {}
    for r in records:
        key = (r.holder, "s1" if r.source == "priv" else "s0")
        groups.setdefault(key, []).append(r)
    manifest: list[dict[str, Any]] = []
    for (holder_key, source), recs in sorted(groups.items()):
        t = world.tenants[recs[0].tenant]
        h = t.holders[holder_key]
        domain = t.units[h.dept].domain if h.dept else None
        vis = "private" if source == "s1" else h.vis
        members = [m for r in recs for m in r.members][:1] if source == "s1" else (list(h.peers) if vis == "members" else [])
        flags = dict(fault_plan.get(holder_key, {})) if source == "s0" else {}
        rng = random.Random(f"{seed}:{holder_key}:{source}")
        base = sorted([r for r in recs if r.phase == "base"], key=lambda r: (-r.days_ago, r.rid))
        late = sorted([r for r in recs if r.phase == "late"], key=lambda r: (-r.days_ago, r.rid))
        base_lines = [json.dumps(record_line(r, by_rid, now, world), sort_keys=True) for r in base]
        base_lines = apply_file_faults(base_lines, flags, rng, rid_of_line=[r.rid for r in base])
        late_lines = [json.dumps(record_line(r, by_rid, now, world), sort_keys=True) for r in late]
        name = f"{holder_key}__{source}"
        header = json.dumps(source_header(holder_key, source, vis, domain, members), sort_keys=True)
        (src_dir / f"{name}.jsonl").write_text("\n".join([header, *base_lines]) + "\n", encoding="utf-8")
        late_file = None
        if late_lines:
            late_file = f"{name}.late.jsonl"
            (src_dir / late_file).write_text("\n".join(late_lines) + "\n", encoding="utf-8")
        if h.owner_type == "user":
            manager = h.owner_key
        else:
            manager = t.admin
        manifest.append({"file": f"{name}.jsonl", "late_file": late_file, "holder_key": holder_key, "holder_id": ids.holders[holder_key], "tenant": t.slug,
                         "manager": manager, "app": app_for(holder_key), "account": account_for(holder_key, source), "source_header_id": f"{holder_key}-{source}",
                         "visibility": vis, "members": members, "domain": domain if source == "s0" else None,
                         "n_base": len(base_lines), "n_late": len(late_lines), "flags": {k: v for k, v in flags.items() if k in ("replay", "restart")}})
    return manifest


def fault_evidence(records: list[Rec], fault_plan: dict[str, Any], ids: Ids) -> dict[str, Any]:
    """What the generator injected, per fault kind, for the architecture gate (G10) to look for in the holders. Non-gold: it names
    holders, source object ids and the raw texts that were retracted or superseded - never an answer."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ("restart", "replay", "duplicate", "out_of_order", "malformed", "delete", "edit")}
    docs: dict[str, list[Rec]] = {}
    for r in records:
        if r.typ == "document":
            docs.setdefault(r.rid, []).append(r)
    for hk, f in sorted(fault_plan.items()):
        hid = ids.holders[hk]
        if f.get("restart"):
            out["restart"].append({"holder_id": hid, "holder_key": hk, "pages_before": 1, "page_size": 3})
        if f.get("replay"):
            out["replay"].append({"holder_id": hid, "holder_key": hk, "mode": "backfill"})
        if f.get("duplicate"):
            out["duplicate"].append({"holder_id": hid, "holder_key": hk, "record_ids": list(f["duplicate"]), "copies": 2})
        if f.get("malformed"):
            out["malformed"].append({"holder_id": hid, "holder_key": hk, "bad_lines": 4})
    for r in records:
        hid = ids.holders[r.holder]
        if r.typ == "delete":
            orig = next((d for d in docs.get(r.rid, []) if d.role != "stale_version"), None)
            out["delete"].append({"holder_id": hid, "holder_key": r.holder, "record_id": r.rid, "marker": orig.text if orig else ""})
        elif r.typ == "edit":
            orig = next((d for d in docs.get(r.rid, []) if d.role != "stale_version"), None)
            out["edit"].append({"holder_id": hid, "holder_key": r.holder, "record_id": r.rid, "stale_marker": orig.text if orig else "", "current_marker": r.text})
        elif r.role == "stale_version":
            cur = next((d for d in docs.get(r.rid, []) if d.role != "stale_version"), None)
            out["out_of_order"].append({"holder_id": hid, "holder_key": r.holder, "record_id": r.rid, "stale_marker": r.text, "current_marker": cur.text if cur else ""})
    return out

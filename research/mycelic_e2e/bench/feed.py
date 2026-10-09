"""Feeder: attaches each source file to its holder through the connector API and syncs it (PLAN_v1 §A stage 1, §D WP2).

Allowed entry points only: ``POST /api/holders/{id}/connectors``, ``PATCH .../connectors/{cid}/sources`` (the owner's choice of
which source to include and its default domain, as in the UI), ``POST .../connectors/{cid}/sync``. This module never opens a
database, never imports ``mycelic.evidence`` / ``mycelic.holder`` / ``mycelic.knowledge``, and sets no holder domain: routable
domains appear only after ingestion, through the holder's own heartbeat.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp


class ApiClient:
    """Bearer-token JSON client for the loopback API."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300))

    async def call(self, method: str, path: str, token: str, body: Any = None, params: dict | None = None) -> tuple[int, Any]:
        async with self._s.request(method, self.base + path, json=body, params=params, headers={"Authorization": f"Bearer {token}"}) as resp:
            try:
                return resp.status, await resp.json()
            except Exception:
                return resp.status, {"raw": (await resp.text())[:300]}

    async def close(self) -> None:
        await self._s.close()


@dataclass
class SyncSummary:
    holder_key: str
    file: str
    connector_id: str | None = None
    syncs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_s: float = 0.0

    def totals(self) -> dict[str, int]:
        t = {"raw_items": 0, "enqueued": 0, "duplicates": 0, "normalize_errors": 0, "processed": 0}
        for s in self.syncs:
            rep = s.get("report") or {}
            for k in ("raw_items", "enqueued", "duplicates", "normalize_errors"):
                t[k] += int(rep.get(k) or 0)
            t["processed"] += int(s.get("processed") or 0)
        return t


async def connect_and_sync(client: ApiClient, entry: dict[str, Any], *, token: str, import_dir: Path, src_dir: Path, principal_map: dict[str, str],
                           restart_hook: Callable[[str, str], Awaitable[None]] | None = None) -> SyncSummary:
    """Base phase for one source file: copy it into the holder's import root, add the connector, include its source with the owner's
    default domain, sync (replay and restart faults act here)."""
    t0 = time.perf_counter()
    hid, flags = entry["holder_id"], entry.get("flags") or {}
    out = SyncSummary(entry["holder_key"], entry["file"])
    import_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_dir / entry["file"], import_dir / entry["file"])
    cfg: dict[str, Any] = {"source_app": entry["app"], "account_id": entry["account"], "paths": [entry["file"]], "auto_include": True,
                           "principal_map": {k: v for k, v in principal_map.items() if k in set(entry.get("members") or [])}}
    if flags.get("restart"):
        cfg["limits"] = {"page_size": 3}
    base = f"/api/holders/{hid}/connectors"
    st, body = await client.call("POST", base, token, {"connector_type": "local_export", "config": cfg, "display_name": entry["source_header_id"]})
    if st != 201:
        out.errors.append(f"add connector: HTTP {st} {str(body)[:200]}")
        return out
    cid = body["connector"]["connector_id"]
    out.connector_id = cid
    st, srcs = await client.call("GET", f"{base}/{cid}/sources", token)
    changes = []
    for s in (srcs or {}).get("items", []):
        ch: dict[str, Any] = {"source_id": s["source_id"], "selection": "included"}
        if entry.get("domain"):
            ch["default_domain_ids"] = [entry["domain"]]          # the owner states what the source is about; holder domains stay unset
        if entry["visibility"] != "private":
            ch["exportable"] = True
        changes.append(ch)
    if changes:
        st, body = await client.call("PATCH", f"{base}/{cid}/sources", token, {"changes": changes})
        if st != 200:
            out.errors.append(f"include source: HTTP {st} {str(body)[:200]}")
    if flags.get("restart") and restart_hook is not None:
        await restart_hook(hid, cid)                                # partial first page, then the holder process restarts
    st, body = await client.call("POST", f"{base}/{cid}/sync", token, {"mode": "incremental"})
    (out.syncs.append(body) if st == 202 else out.errors.append(f"sync: HTTP {st} {str(body)[:200]}"))
    if flags.get("replay"):
        st, body = await client.call("POST", f"{base}/{cid}/sync", token, {"mode": "backfill"})   # re-delivery of the whole file
        (out.syncs.append(body) if st == 202 else out.errors.append(f"replay sync: HTTP {st} {str(body)[:200]}"))
    out.wall_s = round(time.perf_counter() - t0, 3)
    return out


async def late_phase(client: ApiClient, entry: dict[str, Any], summary: SyncSummary, *, token: str, import_dir: Path, src_dir: Path) -> None:
    """Records that arrive after the first sync (edits, deletions): appended to the exported file, then an incremental sync."""
    if not entry.get("late_file") or not summary.connector_id:
        return
    with open(import_dir / entry["file"], "a", encoding="utf-8") as f:
        f.write((src_dir / entry["late_file"]).read_text(encoding="utf-8"))
    st, body = await client.call("POST", f"/api/holders/{entry['holder_id']}/connectors/{summary.connector_id}/sync", token, {"mode": "incremental"})
    (summary.syncs.append(body) if st == 202 else summary.errors.append(f"late sync: HTTP {st} {str(body)[:200]}"))


async def feed_all(client: ApiClient, manifest: list[dict[str, Any]], *, token_for: Callable[[str], str], import_dir_for: Callable[[str], Path],
                   src_dir: Path, principal_map: dict[str, str], restart_hook: Callable[[str, str], Awaitable[None]] | None = None,
                   concurrency: int = 6, log: Callable[[str], None] | None = None) -> list[SyncSummary]:
    sem = asyncio.Semaphore(concurrency)
    done = [0]

    async def one(entry: dict[str, Any]) -> SyncSummary:
        async with sem:
            s = await connect_and_sync(client, entry, token=token_for(entry["manager"]), import_dir=import_dir_for(entry["holder_id"]), src_dir=src_dir,
                                       principal_map=principal_map, restart_hook=restart_hook)
            done[0] += 1
            if log and done[0] % 50 == 0:
                log(f"  fed {done[0]}/{len(manifest)} sources")
            return s
    sums = list(await asyncio.gather(*[one(e) for e in manifest]))
    by_file = {s.file: s for s in sums}
    late_entries = [e for e in manifest if e.get("late_file")]

    async def late(entry: dict[str, Any]) -> None:
        async with sem:
            await late_phase(client, entry, by_file[entry["file"]], token=token_for(entry["manager"]), import_dir=import_dir_for(entry["holder_id"]), src_dir=src_dir)
    await asyncio.gather(*[late(e) for e in late_entries])
    return sums

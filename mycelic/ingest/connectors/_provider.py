"""Helpers shared by the API-backed connectors (GitHub, Slack): principal mapping, URL extraction, time formatting, and
one credential refresh under the connection's refresh lock. Pure functions apart from :func:`refresh_once`."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from ..contract import ConnectorContext, ConnectorError, Credentials

_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`|]+")
_TRAILING = ".,;:!?*_~)"


def principal(ctx: ConnectorContext, app: str, provider_id: Any) -> str:
    """A provider principal as a Mycelic principal id: the owner's ``principal_map`` when it names the id, otherwise the id
    namespaced by app (``slack:U123``), which never equals a Mycelic user id, so an unmapped member can never widen access."""
    pid = str(provider_id)
    pm = ctx.config.get("principal_map") or {}
    mapped = pm.get(pid) if isinstance(pm, dict) else None
    return str(mapped) if mapped else f"{app}:{pid}"


def principals(ctx: ConnectorContext, app: str, ids: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({principal(ctx, app, i) for i in ids if i not in (None, "")}))


def extract_urls(text: str) -> list[str]:
    out: list[str] = []
    for m in _URL.finditer(text or ""):
        u = m.group(0).rstrip(_TRAILING)
        if u and u not in out:
            out.append(u)
    return out


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def gh_time(dt: datetime) -> str:
    """GitHub's ``since`` format (ISO 8601, seconds, ``Z``)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def refresh_once(ctx: ConnectorContext, refresher: Callable[[Credentials], Awaitable[Credentials]] | None) -> bool:
    """§3.1: after an ``AuthExpired``, refresh once under the connection's refresh lock. Returns True when the stored
    credentials changed (refreshed here, or concurrently by another task); False when there is nothing to refresh with."""
    if refresher is None:
        return False
    before = await ctx.secrets.get()
    if before.refresh_token is None:
        return False
    async with ctx.secrets.refresh_lock():
        current = await ctx.secrets.get()
        if current.access_token != before.access_token:
            return True                                   # someone else refreshed while we waited for the lock
        try:
            new = await refresher(current)
        except ConnectorError:                            # refresh refused (revoked grant, bad client): the caller re-raises
            return False
        await ctx.secrets.replace(new)
        return True

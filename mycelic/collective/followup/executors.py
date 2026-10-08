"""Follow-up executors (G7): T0 evidence packets from the target sites, T1 approved drafts to an outbox.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

An executor has ``run(ExecContext) -> dict``; the service calls it at most once per follow-up, after an ``executing``
entry is committed, and records the dict as the ``executed`` result.

:class:`PacketExecutor` (T0) sends each target site, in sorted order, one ``packet_request`` (the follow-up key, the
conclusion's question id, candidate key and window, ``as_of`` the UTC date) through the site's handler (``site id ->
callable(request) -> packet body``: in-process here, ``edge/packets.py``'s ``PacketAssembler.handle``). Each handler
runs on a daemon thread joined against the deadline, the orchestrator's pattern, so a hung site delays the others by
at most the deadline; a result that arrives later is left out of the result (the follow-up has executed), but the
thread is not cancelled: a late handler still finishes, so its site keeps the full packet and its packet may still
cross the Boundary into HQ's receive log (G0 scans it there) while the result lists that site as ``timeout``. A site
is unavailable with a reason: ``no_handler``, ``error`` (any exception; nothing of it is kept), ``timeout`` or
``invalid`` (the Boundary's own validator refuses the body for that site, or its site, key, question id, candidate key
or window is not the request's). The result is ``{status: complete | partial | failed, packets: [bodies by site],
unavailable: [{site, reason}]}``; ``failed`` means no packet at all, and it is final for the key (another set of
targets is another key).

:class:`OutboxExecutor` (T1) appends the approved draft as one canonical JSON line, with the built-ahead label, to an
outbox file (flushed and synced) and returns ``{outbox, line_sha256, version}``; ``line_sha256`` is the sha256 of the
line without its newline. T2 has no executor.

Deterministic: no clock, no randomness, sites in sorted order.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..edge.egress import SCHEMA_VERSION, check_artifact
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from ..packs.connector import SITE_ID_RE
from .policy import BUILT_AHEAD_LABEL, utc_date

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack
    from .service import ConclusionView, FollowupState

MAX_DEADLINE_SECONDS = 3600.0
UNAVAILABLE_REASONS = ("no_handler", "error", "timeout", "invalid")
PACKET_RESULTS = ("complete", "partial", "failed")


@dataclass(frozen=True)
class ExecContext:
    view: "ConclusionView"
    state: "FollowupState"
    as_of: str
    draft: Mapping[str, Any] | None


def _call(handler: Callable[[dict[str, Any]], Any], request: dict[str, Any], holder: dict[str, Any]) -> None:
    try:
        holder["result"] = handler(request)
    except Exception:            # an error at the site is an unavailable site; nothing of it is kept
        holder["error"] = True


class PacketExecutor:
    def __init__(self, pack: "FrozenPack", handlers: Mapping[str, Callable[[dict[str, Any]], Any]], *,
                 deadline_seconds: float = 600.0) -> None:
        if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
                or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= MAX_DEADLINE_SECONDS):
            raise ValueError("deadline_seconds must be a finite number in (0, 3600]") from None
        for site in handlers:
            if not isinstance(site, str) or SITE_ID_RE.fullmatch(site) is None or not callable(handlers[site]):
                raise ValueError("handlers must map site ids to callables") from None
        self.pack = pack
        self.handlers = dict(handlers)
        self.deadline_seconds = float(deadline_seconds)

    def request(self, ctx: ExecContext) -> dict[str, Any]:
        view = ctx.view
        return {"schema_version": SCHEMA_VERSION, "pack": self.pack.id, "pack_hash": self.pack.config_hash,
                "followup_key": ctx.state.key, "question_id": view.question_id,
                "candidate_key": view.candidate_key,
                "window": {"start_week": view.window[0], "end_week": view.window[1]}, "as_of": utc_date(ctx.as_of)}

    def _packet(self, site: str, request: Mapping[str, Any], result: Any) -> dict[str, Any] | None:
        body = None
        try:
            body = strict_load(canonical_bytes(result))
        except StrictJsonError:
            body = None
        if body is None or check_artifact(self.pack, site, "packet", body) is not None:
            return None
        if any(body[k] != request[k] for k in ("followup_key", "question_id", "candidate_key", "window")):
            return None
        return body

    def run(self, ctx: ExecContext) -> dict[str, Any]:
        request = self.request(ctx)
        packets: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for site in sorted(ctx.state.targets):
            handler = self.handlers.get(site)
            if handler is None:
                unavailable.append({"site": site, "reason": "no_handler"})
                continue
            holder: dict[str, Any] = {}
            thread = threading.Thread(target=_call, args=(handler, strict_load(canonical_bytes(request)), holder),
                                      name=f"packet-{site}", daemon=True)
            thread.start()
            thread.join(timeout=self.deadline_seconds)
            if thread.is_alive():
                unavailable.append({"site": site, "reason": "timeout"})
                continue
            if "result" not in holder:
                unavailable.append({"site": site, "reason": "error"})
                continue
            body = self._packet(site, request, holder["result"])
            if body is None:
                unavailable.append({"site": site, "reason": "invalid"})
            else:
                packets.append(body)
        status = "failed" if not packets else ("complete" if not unavailable else "partial")
        return {"status": status, "packets": sorted(packets, key=lambda p: p["site"]),
                "unavailable": sorted(unavailable, key=lambda u: (u["site"], u["reason"]))}


class OutboxExecutor:
    def __init__(self, outbox_path: str | Path) -> None:
        self.path = Path(outbox_path)
        self._lock = threading.Lock()

    def run(self, ctx: ExecContext) -> dict[str, Any]:
        state = ctx.state
        line = canonical_dumps({
            "schema_version": 1, "key": state.key, "conclusion_id": state.conclusion_id, "type": state.type_id,
            "tier": state.tier, "version": state.decided_version, "draft": ctx.draft, "owner": state.owner,
            "approved_by": state.decided_by, "targets": list(state.targets), "label": BUILT_AHEAD_LABEL})
        data = line.encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as fh:
                fh.write(data + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
        return {"outbox": self.path.name, "line_sha256": sha256_hex(data), "version": state.decided_version}

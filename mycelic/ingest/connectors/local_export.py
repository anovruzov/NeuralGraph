"""Local export connector: user-provided JSON / JSONL exports of messages and documents, with edits and deletions.

Status ``tested-offline``. It proves the connector framework end to end without credentials: it reads only the files the
owner names in its configuration, never the network, and implements the whole contract (connect, authorize, discover,
windowed backfill, incremental sync, notify-then-fetch with confirmed deletions, signed notices, normalize, health). It
claims support for no third-party application: ``source_app`` is whatever label the owner gives an export (two
instances with different labels are two apps as far as record identity is concerned).

Configuration (non-secret)::

    {"source_app": "teamchat",            # record identity family; default 'local_export'
     "account_id": "acme-export",         # identity namespace of object ids in these files; default 'local'
     "paths": ["/data/eng.jsonl", ...],   # one source per file; nothing else is ever read
     "principal_map": {"u1": "usr_ana"},  # provider member ids -> Mycelic principal ids (ACL checks)
     "default_visibility": "private",     # when a file has no header
     "auto_include": true | ["eng-*"],    # owner auto-include rule (otherwise sources wait for review)
     "limits": {"page_size": 100, "backfill_window_days": 30, ...}}

File format. JSON: ``{"source": {...}, "records": [...]}`` or a bare list of records. JSONL: one object per line, the first
line may be the source header ``{"type": "source", ...}``. Source header::

    {"id": "eng-chat", "name": "#eng", "type": "channel", "visibility": "public|members|private",
     "member_ids": ["u1", "u2"], "membership_ref": "eng-chat-members"}

Records (later lines win per object; an edit is the same id with a later ``updated_at``)::

    {"type": "message" | "document" | "event" | "edit" | "version" | "delete" | "redact" | "conversation",
     "id": "m-1", "object_type": "message", "version": "3", "event_id": "...",
     "conversation_id": "c-1", "thread_id": null, "parent_id": null, "author": "u1", "participants": ["u1", "u2"],
     "created_at": "...", "updated_at": "...", "deleted_at": "...",
     "title": "...", "text": "...", "content_type": "text/plain|text/markdown|text/html",
     "attachments": [{"id": "a1", "filename": "x.pdf", "content_type": "application/pdf", "size": 10, "sha256": "..."}],
     "labels": ["deploy"], "state": "open", "container_name": "#eng", "domains": ["infrastructure"],
     "permissions": {"visibility": "members", "member_ids": ["u1"]}, "sensitivity": "internal", "is_bot": false,
     "forwarded_from": {"app": "...", "account": "...", "object_type": "message", "object_id": "..."}}

Content is data: no field of a record changes what this connector does.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from ...util import canonical_json
from ..acl import narrow
from ..contract import (AuthStart, BackfillWindow, Capabilities, ConnectorContext, ConnectorManifest, ConnectResult, Connector, Cursor,
                        HealthReport, Page, PermanentError, RawItem, SourceDescriptor, SourceUnavailable, WebhookNotice, windows_between)
from ..events import VISIBILITIES, AttachmentRef, CanonicalEvent, DerivedFrom, Permissions, normalize_ts

CURSOR_VERSION = 1
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
_KIND = {"message": "message", "edit": "message", "document": "document", "event": "event", "version": "message_version",
         "conversation": "conversation"}
SIGNATURE_TOLERANCE_SECONDS = 300


class LocalExportConnector(Connector):
    manifest = ConnectorManifest(
        connector_type="local_export",
        display_name="Local JSON/JSONL export",
        version="1.0.0",
        status="tested-offline",
        auth_kinds=("none",),
        scopes=(),
        modes=frozenset({"export", "pull", "webhook"}),
        source_types=("export_file", "channel", "dm", "thread", "folder", "mailbox", "space"),
        capabilities=Capabilities(edits=True, deletes="webhook", threads=True, attachments=True, acl="full", exports=True),
        allowed_hosts=(),
        default_poll_seconds=300,
        terms_notes="Reads only the export files the owner lists; files are never modified or uploaded anywhere.",
        ownership=("personal", "org"),
    )

    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, int, dict[str, Any], list[Any], list[str], list[str | None]]] = {}

    # ------------------------------------------------------------------ configuration and files
    @staticmethod
    def _paths(ctx: ConnectorContext) -> list[Path]:
        raw = ctx.config.get("paths") or []
        if isinstance(raw, str):
            raw = [raw]
        return [Path(str(p)).expanduser().resolve() for p in raw]

    def _load_sync(self, path: Path, max_bytes: int) -> tuple[dict[str, Any], list[Any], list[str], list[str | None]]:
        """``(header, records, prefix_digests, record_times)``, cached per (mtime, size). Unparsable lines become
        ``{"__invalid__": line_no}`` so they are counted as normalize errors instead of stopping the import."""
        st = path.stat()
        if st.st_size > max_bytes:
            raise PermanentError("export file exceeds the size limit", code="export_too_large", detail={"bytes": st.st_size})
        key = str(path)
        hit = self._cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2], hit[3], hit[4], hit[5]
        text = path.read_text(encoding="utf-8")
        header: dict[str, Any] = {}
        records: list[Any] = []
        if path.suffix.lower() in (".jsonl", ".ndjson"):
            for n, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    records.append({"__invalid__": n})
                    continue
                if not records and not header and isinstance(obj, dict) and obj.get("type") == "source":
                    header = obj
                    continue
                records.append(obj if isinstance(obj, dict) else {"__invalid__": n})
        else:
            try:
                data = json.loads(text)
            except ValueError:
                raise PermanentError("export file is not valid JSON", code="export_invalid") from None
            if isinstance(data, dict):
                header = data.get("source") if isinstance(data.get("source"), dict) else {}
                data = data.get("records") or []
            records = [r if isinstance(r, dict) else {"__invalid__": i} for i, r in enumerate(data or [], start=1)]
        prefix = [""]
        for r in records:
            prefix.append(hashlib.sha256((prefix[-1] + canonical_json(r)).encode("utf-8")).hexdigest())
        times = [self._record_time(r) for r in records]
        self._cache[key] = (st.st_mtime_ns, st.st_size, header, records, prefix, times)
        return header, records, prefix, times

    async def _load(self, ctx: ConnectorContext, path: Path) -> tuple[dict[str, Any], list[Any], list[str], list[str | None]]:
        try:
            return await asyncio.to_thread(self._load_sync, path, int(ctx.limits.max_export_bytes))
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            raise SourceUnavailable("access_lost", detail={"file": path.name}) from None

    @staticmethod
    def _record_time(rec: Any) -> str | None:
        if not isinstance(rec, dict):
            return None
        return normalize_ts(rec.get("updated_at") or rec.get("deleted_at") or rec.get("created_at"))

    def _descriptor(self, ctx: ConnectorContext, path: Path, header: Mapping[str, Any]) -> SourceDescriptor:
        pm = ctx.config.get("principal_map") or {}
        vis = str(header.get("visibility") or ctx.config.get("default_visibility") or "private")
        if vis not in VISIBILITIES:
            vis = "private"
        ref = header.get("membership_ref")
        stype = header.get("source_type") or (header.get("type") if header.get("type") not in (None, "source") else None) or "export_file"
        return SourceDescriptor(source_type=str(stype),
                                external_id=str(header.get("id") or path.stem), name=str(header.get("name") or path.name),
                                parent_external_id=header.get("parent_id"), visibility=vis,
                                member_ids=tuple(str(pm.get(m, m)) for m in header.get("member_ids") or ()),
                                membership_ref=f"{ctx.source_app}:{ref}" if ref else None,
                                suggested_domain_ids=tuple(header.get("domains") or ()), metadata={"path": str(path)})

    async def _path_for(self, ctx: ConnectorContext, source: SourceDescriptor) -> Path:
        allowed = self._paths(ctx)
        hinted = source.metadata.get("path") if source.metadata else None
        if hinted and Path(str(hinted)).resolve() in allowed:
            return Path(str(hinted)).resolve()
        for p in allowed:                         # only configured files are ever opened
            try:
                header = (await self._load(ctx, p))[0]
            except SourceUnavailable:
                continue
            if str(header.get("id") or p.stem) == source.external_id:
                return p
        raise SourceUnavailable("access_lost", detail={"source": source.external_id})

    # ------------------------------------------------------------------ lifecycle
    async def authorize(self, *, tenant_id: str, holder_id: str, redirect_uri: str, state: str) -> AuthStart:
        return AuthStart(kind="file_upload", instructions="Provide JSON or JSONL export files in the documented local export format.")

    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        cfg = ctx.config
        app = cfg.get("source_app")
        if app is not None and not _SLUG.match(str(app)):
            raise PermanentError("source_app must be a lower-case label", code="bad_config")
        if cfg.get("principal_map") is not None and not isinstance(cfg.get("principal_map"), dict):
            raise PermanentError("principal_map must be an object", code="bad_config")
        paths = self._paths(ctx)
        if not paths:
            raise PermanentError("no export files configured", code="bad_config")
        for p in paths:
            if not p.is_file() or not os.access(p, os.R_OK):
                raise PermanentError("an export file is missing or unreadable", code="bad_config", detail={"file": p.name})
            if p.stat().st_size > ctx.limits.max_export_bytes:
                raise PermanentError("export file exceeds the size limit", code="export_too_large", detail={"file": p.name})
        return ConnectResult(source_account_id=str(cfg.get("account_id") or "local"), auth_account_id="",
                             account_label=f"{len(paths)} local export file(s)")

    async def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:
        for p in self._paths(ctx):
            try:
                header = (await self._load(ctx, p))[0]
            except (SourceUnavailable, PermanentError) as exc:
                ctx.log.warning("skipping an export file during discovery: %s", exc.code)
                continue
            yield self._descriptor(ctx, p, header)

    async def health(self, ctx: ConnectorContext) -> HealthReport:
        ok = all(p.is_file() and os.access(p, os.R_OK) for p in self._paths(ctx))
        return HealthReport(status="ok" if ok else "error", checks={"files_readable": ok}, code=None if ok else "file_unreadable")

    # ------------------------------------------------------------------ data
    def _items(self, ctx: ConnectorContext, source: SourceDescriptor, records: list[Any], idx: list[int]) -> list[RawItem]:
        fetched = ctx.clock().astimezone(timezone.utc).isoformat(timespec="microseconds")
        out = []
        for i in idx:
            r = records[i]
            out.append(RawItem(object_type=str(r.get("type") or "message") if isinstance(r, dict) else "invalid", payload=r, source=source,
                               fetched_at=fetched, extra={"position": i}))
        return out

    async def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:
        """Everything after the cursor's position. An export is a snapshot the owner asked to import, so a first pass
        (no cursor) reads the whole file; afterwards only appended records are read. A rewritten file (the records before
        the cursor changed) is read again from the start and de-duplicated downstream."""
        path = await self._path_for(ctx, source)
        _header, records, prefix, times = await self._load(ctx, path)
        pos = 0
        hw = cursor.high_watermark if cursor else None
        if cursor is not None and cursor.data.get("path") == str(path):
            n = int(cursor.data.get("position") or 0)
            if 0 <= n <= len(records) and cursor.data.get("prefix") == prefix[n]:
                pos = n
        size = max(1, int(ctx.limits.page_size))
        stream = f"incr:{source.external_id}"
        while pos < len(records):
            if ctx.cancelled.is_set():
                return
            end = min(len(records), pos + size)
            hw = max([t for t in times[pos:end] if t] + ([hw] if hw else []), default=hw)
            yield Page(stream=stream, items=self._items(ctx, source, records, list(range(pos, end))),
                       next_cursor=Cursor(CURSOR_VERSION, {"path": str(path), "position": end, "prefix": prefix[end]}, hw), has_more=end < len(records))
            pos = end

    def plan_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, *, anchor: datetime) -> list[BackfillWindow]:
        """Windows from the connect time back to the oldest record in the file (stable boundaries, newest first)."""
        try:
            path = next(p for p in self._paths(ctx) if str(p) == source.metadata.get("path")) if source.metadata.get("path") else None
            times = self._load_sync(path, int(ctx.limits.max_export_bytes))[3] if path else []
        except Exception:
            times = []
        known = [t for t in times if t]
        if not known:
            return []
        oldest = datetime.fromisoformat(min(known))
        if oldest >= anchor:
            return []
        return windows_between(source, start=oldest, end=anchor, window_days=ctx.limits.backfill_window_days)

    async def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,
                               cursor: Cursor | None) -> AsyncIterator[Page]:
        path = await self._path_for(ctx, source)
        _header, records, prefix, times = await self._load(ctx, path)
        lo, hi = normalize_ts(window.start), normalize_ts(window.end)
        idx = [i for i, t in enumerate(times) if t and lo <= t < hi]
        digest = prefix[-1]
        offset = 0
        if cursor is not None and cursor.data.get("path") == str(path) and cursor.data.get("digest") == digest:
            offset = max(0, min(len(idx), int(cursor.data.get("offset") or 0)))
        size = max(1, int(ctx.limits.page_size))
        hw = cursor.high_watermark if cursor else None
        while offset < len(idx):
            if ctx.cancelled.is_set():
                return
            part = idx[offset:offset + size]
            end = offset + len(part)
            hw = max([times[i] for i in part if times[i]] + ([hw] if hw else []), default=hw)
            yield Page(stream=window.stream, items=self._items(ctx, source, records, part),
                       next_cursor=Cursor(CURSOR_VERSION, {"path": str(path), "digest": digest, "offset": end}, hw), has_more=end < len(idx))
            offset = end

    # ------------------------------------------------------------------ notices (notify, then fetch)
    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        """Generic signed notice: ``X-Mycelic-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, f"{t}." + body)>``, 300 s window."""
        h = {k.lower(): v for k, v in headers.items()}
        parts = dict(p.split("=", 1) for p in str(h.get("x-mycelic-signature", "")).split(",") if "=" in p)
        ts, sig = parts.get("t", ""), parts.get("v1", "")
        if not ts.isdigit() or not sig or abs(now - int(ts)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
        expected = hmac.new(secret, ts.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        """Ids only: whatever else the body carries (titles, text) is dropped here."""
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        refs = []
        for o in data.get("objects") or []:
            if isinstance(o, dict) and o.get("id"):
                refs.append({"type": str(o.get("type") or "message")[:40], "id": str(o["id"])[:200]})
        if not refs:
            return []
        return [WebhookNotice(connector_type=cls.manifest.connector_type, delivery_id=str(data.get("delivery_id") or "")[:200],
                              external_account_id=str(data.get("account_id") or "")[:200], source_external_id=str(data.get("source_id") or "")[:200],
                              action=str(data.get("action") or "changed")[:60], object_refs=tuple(refs), occurred_at=normalize_ts(data.get("occurred_at")))]

    async def handle_webhook(self, ctx: ConnectorContext, notice: WebhookNotice) -> AsyncIterator[Page]:
        """Fetch the authoritative state of the named objects from the current file. An object that is no longer in a
        readable file is confirmed deleted; an unreadable file is access loss, never a deletion."""
        source = None
        async for s in self.discover_sources(ctx):
            if s.external_id == notice.source_external_id:
                source = s
                break
        if source is None:
            raise SourceUnavailable("access_lost", detail={"source": notice.source_external_id})
        path = await self._path_for(ctx, source)
        _header, records, _prefix, _times = await self._load(ctx, path)
        idx: list[int] = []
        synthetic: list[dict[str, Any]] = []
        for ref in notice.object_refs:
            otype, oid = str(ref.get("type") or "message"), str(ref.get("id"))
            hits = [i for i, r in enumerate(records) if isinstance(r, dict) and str(r.get("id")) == oid and _object_type(r) == otype]
            if hits:
                idx.extend(hits)
            else:
                synthetic.append({"type": "delete", "id": oid, "object_type": otype, "deleted_at": notice.occurred_at, "confirmed": "absent"})
        items = self._items(ctx, source, records, sorted(set(idx)))
        fetched = ctx.clock().astimezone(timezone.utc).isoformat(timespec="microseconds")
        items += [RawItem(object_type="delete", payload=p, source=source, fetched_at=fetched) for p in synthetic]
        yield Page(stream="webhook", items=items, next_cursor=None, has_more=False)

    # ------------------------------------------------------------------ normalize (pure)
    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list[CanonicalEvent]:
        rec = raw.payload
        if not isinstance(rec, dict) or "__invalid__" in rec:
            raise PermanentError("invalid export record", code="normalize_failed", detail={"position": raw.extra.get("position")})
        rtype = str(rec.get("type") or "message").lower()
        oid = rec.get("id")
        if oid in (None, ""):
            raise PermanentError("export record without id", code="normalize_failed", detail={"position": raw.extra.get("position")})
        pm = ctx.config.get("principal_map") or {}
        src = raw.source
        perms = Permissions(src.visibility if src.visibility in VISIBILITIES else "private", tuple(src.member_ids), src.membership_ref)
        rp = rec.get("permissions")
        if isinstance(rp, dict):
            ref = rp.get("membership_ref")
            perms = narrow(perms, Permissions(str(rp.get("visibility") or perms.visibility),
                                              tuple(str(pm.get(m, m)) for m in rp.get("member_ids") or ()),
                                              f"{ctx.source_app}:{ref}" if ref else None))
        common: dict[str, Any] = dict(tenant_id=ctx.tenant_id, holder_id=ctx.holder_id, connector_id=ctx.connector_id, source_app=ctx.source_app,
                                      source_account_id=ctx.source_account_id, source_object_type=_object_type(rec), source_object_id=str(oid),
                                      observed_at=raw.fetched_at, source_event_id=str(rec["event_id"]) if rec.get("event_id") else None,
                                      permissions=perms)
        if rtype in ("delete", "deletion"):
            return [CanonicalEvent.create(kind="deletion", updated_at=rec.get("deleted_at") or rec.get("updated_at"), tiebreak="deleted",
                                          hints={"reason": "deleted_at_source"}, **common)]
        if rtype in ("redact", "redaction"):
            return [CanonicalEvent.create(kind="redaction", updated_at=rec.get("redacted_at") or rec.get("updated_at"), tiebreak="redacted",
                                          hints={"reason": "redaction"}, **common)]
        kind = _KIND.get(rtype)
        if kind is None:
            raise PermanentError("unknown export record type", code="normalize_failed", detail={"position": raw.extra.get("position")})
        if rtype == "edit" and common["source_object_type"] in ("document", "event"):
            kind = common["source_object_type"]
        atts = tuple(AttachmentRef(attachment_id=str(a.get("id") or a.get("sha256") or a.get("filename") or i), filename=str(a.get("filename") or ""),
                                   content_type=str(a.get("content_type") or "application/octet-stream"), size_bytes=a.get("size"),
                                   sha256=a.get("sha256"), text_extractable=bool(a.get("text_extractable")))
                     for i, a in enumerate(rec.get("attachments") or []) if isinstance(a, dict))
        derived = []
        fwd = rec.get("forwarded_from")
        if isinstance(fwd, dict):
            derived.append(DerivedFrom(relation=str(fwd.get("relation") or "forward"), source_app=fwd.get("app"), source_account_id=fwd.get("account"),
                                       source_object_type=fwd.get("object_type"), source_object_id=str(fwd["object_id"]) if fwd.get("object_id") else None,
                                       url=fwd.get("url")))
        hints = {"labels": [str(x) for x in rec.get("labels") or []], "state": rec.get("state"),
                 "container_name": rec.get("container_name") or src.name, "is_bot": bool(rec.get("is_bot")),
                 "domain_hints": [str(x) for x in rec.get("domains") or []]}
        # the provider version token; exports without one use the object's last-modified time, so a metadata-only change
        # (labels, ACL) is a new event while re-reading an unchanged line is the same event
        version = str(rec.get("version") or normalize_ts(rec.get("updated_at")) or "")
        return [CanonicalEvent.create(kind=kind, source_version=version, conversation_id=_s(rec.get("conversation_id")),
                                      thread_id=_s(rec.get("thread_id")), parent_message_id=_s(rec.get("parent_id")), author_id=_s(rec.get("author")),
                                      participant_ids=[str(p) for p in rec.get("participants") or []], created_at=rec.get("created_at"),
                                      updated_at=rec.get("updated_at"), title=str(rec.get("title") or ""), body=str(rec.get("text") or rec.get("body") or ""),
                                      content_type=str(rec.get("content_type") or "text/plain"), attachment_references=atts,
                                      sensitivity=str(rec.get("sensitivity") or "internal"), derived_from=derived, hints=hints,
                                      tiebreak=str(rec["version"]) if rec.get("version") else None, **common)]


def _object_type(rec: Mapping[str, Any]) -> str:
    if rec.get("object_type"):
        return str(rec["object_type"])
    t = str(rec.get("type") or "message").lower()
    return t if t in ("message", "document", "event", "conversation") else "message"


def _s(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


def sign_notice(secret: bytes, body: bytes, *, now: float | None = None) -> str:
    """The ``X-Mycelic-Signature`` header value for a local notice (used by the owner's file watcher and the tests)."""
    ts = str(int(now if now is not None else time.time()))
    return f"t={ts},v1=" + hmac.new(secret, ts.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


__all__ = ["LocalExportConnector", "sign_notice"]

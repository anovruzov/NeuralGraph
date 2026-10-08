"""Google Drive connector: the shared drives and folders the owner includes, read through the Drive API v3 with the single
scope ``https://www.googleapis.com/auth/drive.readonly``; personal (My Drive folders) and organization-wide (shared drives
for unit holders). Kept current by the changes feed and ``changes.watch`` push channels.

Status ``tested-offline``: exercised end to end against the mock in :mod:`mycelic.ingest.mocks.drive_mock` (field masks,
``q`` queries, ``pageToken`` paging, shared-drive flags, Google's error JSON and rate limits, channel notifications). It has
not been run against Google by this code base.

Endpoints (``https://www.googleapis.com/drive/v3/...``; every call sends ``supportsAllDrives=true``):

* connect: ``about?fields=user(...)`` (identity only);
* discover: ``drives`` (shared drives the account is a member of) and the folders named in ``config.folder_ids``; the
  My Drive root only when ``config.include_my_drive`` is true. Nothing else: no silent whole-drive ingestion. Every source
  still goes through the owner's selection (pending review unless an auto-include rule matches);
* backfill: one window ``[connect time − backfill_days, connect time)`` on ``modifiedTime``, crawled breadth-first with
  ``files?q='<folder>' in parents and trashed = false&fields=...&pageToken&includeItemsFromAllDrives=true`` (``corpora=drive``
  with ``driveId`` inside a shared drive), under ``max_depth`` (default 5), ``max_folders`` (500) and ``max_files`` (5000).
  The crawl state (folder queue, page token) is the cursor, so a crash resumes at the page it stopped;
* content: Google Docs through ``files/{id}/export?mimeType=text/plain``; text, Markdown, CSV and JSON through
  ``files/{id}?alt=media``; PDF and DOCX through ``alt=media`` and :func:`mycelic.evidence.extract.extract_text`. Every
  download is capped at ``max_file_bytes`` (default 5 MB; Drive's ``size`` is checked first). Other types (images, sheets,
  slides, binaries) are skipped, as are files whose text is empty or cannot be extracted;
* incremental, per source: ``changes/startPageToken`` (taken *before* a catch-up crawl of the recent window,
  ``initial_lookback_days``, on the first pass and after a :class:`CursorInvalid`), then
  ``changes?pageToken&includeRemoved=true&includeItemsFromAllDrives=true`` (``driveId`` for shared drives). An unknown or
  expired page token (400/404/410) raises :class:`CursorInvalid`.

Deletions and moves. A change with ``removed`` (or without a file) is confirmed with ``files/{id}``: a 404 while the source
drive or folder still answers is a deletion; an unreadable source raises ``SourceUnavailable(access_lost)`` and deletes
nothing. A trashed file is a deletion (sticky: restoring it from the trash does not bring the record back). A file whose
ancestry (walked with ``files/{id}?fields=parents``) no longer reaches the source left it: ``excluded_source``. These
deletions are inferred from a feed that covers the whole account, so they carry ``hints.if_known`` (applied only to a
record the holder has) and ``hints.still_in`` (its ancestor folders and drive: a file moved into another included source
of the same connection is kept). A changed *folder* is crawled (bounded by ``max_files_per_change``, default 500): moved
into the source, its files are read; moved out, each file leaves the source. A renamed folder is therefore re-read
(event-key dedupe absorbs it).

Identity. Drive file ids are unique across Google, so ``source_account_id`` is ``drive.google.com`` (the API host for a
non-default ``api_base``): the same file reached through a unit's shared-drive connection and a person's own connection
keeps one canonical identity and root. A file is a ``document`` (``drive_file``, its id), ``source_version`` is Drive's
``version`` (it changes with content, permissions and moves) and the order key is ``modifiedTime | version`` (20 digits), so
a permission change is a metadata update of the record.

Permissions (``permissions.list`` per file, inherited ones included). ``anyone`` or ``domain`` → ``public``; users and
groups → ``members`` (user addresses through ``config.principal_map``; each group as the ``membership_ref``
``google_drive:group:<address>``, resolved at use time from ``acl_memberships``, so until an administrator maps a group's
members nobody but the owner is admitted through it; several groups, or users plus groups, intersect: narrower, never
wider); only the owner → ``private``. If Drive refuses to list a file's permissions, the record is ``private`` to the owner
(fail closed). A source's ACL (the shared drive's or folder's own permissions; group grants on a source are not expanded,
and a source shared with a group does not narrow its files) narrows every record of it at use time, so a member removed
from a shared drive loses access at the next discovery without re-reading files; a file's own narrowing arrives with its
change and is a metadata update.

Push (``changes.watch``). Drive POSTs an empty body with ``X-Goog-Channel-ID``, ``X-Goog-Channel-Token``,
``X-Goog-Resource-State`` (``sync`` once, then ``change``), ``X-Goog-Resource-ID`` and ``X-Goog-Message-Number``. The channel
token (the endpoint secret, set by :meth:`watch_changes`) is compared in constant time; the notice is ids only
(``delivery_id = <channel id>:<message number>``) and account-wide, so the holder runs a changes fetch of the included
sources. Channels expire (at most a week for changes); nothing in this code base renews them yet.

Configuration (non-secret)::

    {"api_base": "https://www.googleapis.com", "folder_ids": ["1AbC..."], "include_my_drive": false,
     "principal_map": {"ben@acme.example": "usr_ben"}, "max_depth": 5, "max_folders": 500, "max_files": 5000,
     "max_file_bytes": 5000000, "max_files_per_change": 500, "initial_lookback_days": 30}
"""
from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit

from ...evidence.extract import ExtractError, extract_text
from ..contract import (AuthExpired, AuthRevoked, BackfillWindow, Capabilities, ConnectorContext, ConnectorError, ConnectorManifest, ConnectResult,
                        Connector, Cursor, CursorInvalid, HealthReport, HttpResult, InsufficientScope, ObjectGone, Page, PermanentError, RateLimited,
                        RateLimitSpec, RawItem, ScopeSpec, SourceDescriptor, SourceUnavailable, TransientError, WebhookNotice)
from ..events import CanonicalEvent, Permissions, normalize_ts
from ._google import GOOGLE_AUTH_HOSTS, GoogleOAuthMixin, email_principals, header, valid_email
from ._provider import extract_urls, iso_utc, refresh_once

DEFAULT_API = "https://www.googleapis.com"
READONLY = "https://www.googleapis.com/auth/drive.readonly"
SATISFYING = frozenset({READONLY, "https://www.googleapis.com/auth/drive"})
WRITE_SCOPES = frozenset({"https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/drive.file",
                          "https://www.googleapis.com/auth/drive.appdata", "https://www.googleapis.com/auth/drive.metadata"})
CURSOR_VERSION = 1
FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOWNLOADABLE = {"text/plain": "file.txt", "text/markdown": "file.md", "text/x-markdown": "file.md", "text/csv": "file.csv",
                "application/json": "file.json", "application/pdf": "file.pdf", DOCX: "file.docx"}
FILE_FIELDS = ("id,name,mimeType,parents,driveId,createdTime,modifiedTime,version,size,trashed,owners(emailAddress),"
               "lastModifyingUser(emailAddress)")
LIST_FIELDS = f"nextPageToken,files({FILE_FIELDS})"
CHANGE_FIELDS = f"nextPageToken,newStartPageToken,changes(changeType,removed,fileId,time,driveId,file({FILE_FIELDS}))"
PERM_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,domain,deleted)"
MAX_ANCESTRY = 32
_ID = re.compile(r"^[A-Za-z0-9_\-]{4,128}$")
_CHANNEL = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")
_DIGITS = re.compile(r"^\d{1,20}$")
_FAR_FUTURE = "9999-12-31T23:59:59.999999+00:00"


class GoogleDriveConnector(GoogleOAuthMixin, Connector):
    oauth_scopes = (READONLY,)
    satisfying_scopes = SATISFYING
    write_scopes = WRITE_SCOPES
    manifest = ConnectorManifest(
        connector_type="google_drive",
        display_name="Google Drive (shared drives and folders you choose)",
        version="1.0.0",
        status="tested-offline",
        auth_kinds=("oauth2",),
        scopes=(ScopeSpec(READONLY, True, "Read the shared drives and folders you choose to include, and who has access to each file; "
                                          "nothing in your Drive is ever changed."),),
        modes=frozenset({"pull", "webhook"}),
        source_types=("shared_drive", "folder"),
        capabilities=Capabilities(edits=True, deletes="webhook", threads=False, attachments=False, acl="full", exports=False),
        rate_limit=RateLimitSpec(kind="tiered", default_rps=10.0, burst=10, serial_per_token=True),
        allowed_hosts=("www.googleapis.com",) + GOOGLE_AUTH_HOSTS,
        default_poll_seconds=300,
        terms_notes=("Reads the shared drives and the folders you name (your whole My Drive only if you ask), with the read-only scope. "
                     "Google Docs, text, Markdown, CSV, JSON, PDF and Word files up to a size cap become documents; other files are "
                     "skipped. Each record keeps its file's sharing: anyone or domain links are public, people and groups limit it to "
                     "them, and a shared drive's or folder's membership narrows its files. Group members are not listed with read-only "
                     "access, so a group's members see a file only once an administrator maps the group. Google's API Services User "
                     "Data Policy applies and drive.readonly is a restricted scope that needs Google's verification for apps used "
                     "beyond testing; confirm your use fits it. Push channels (changes.watch) expire and are not renewed automatically "
                     "yet. Status: tested offline against a mock of the documented API; not live-verified."),
        ownership=("personal", "org"),
    )

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _base(ctx: ConnectorContext) -> str:
        return str(ctx.config.get("api_base") or DEFAULT_API).rstrip("/") + "/drive/v3"

    @staticmethod
    def _namespace(ctx: ConnectorContext) -> str:
        api = str(ctx.config.get("api_base") or DEFAULT_API).rstrip("/")
        return "drive.google.com" if api == DEFAULT_API else (urlsplit(api).hostname or "drive.google.com").lower()

    async def _req(self, ctx: ConnectorContext, method: str, path: str, *, params: Mapping[str, Any] | None = None, json: Any | None = None,
                   expected: tuple[int, ...] = (200,), max_bytes: int | None = None) -> HttpResult:
        url = self._base(ctx) + path

        async def go() -> HttpResult:
            return await ctx.http.request(method, url, params=params, json=json, expected=expected, max_bytes=max_bytes)
        try:
            return await go()
        except AuthExpired:
            if await refresh_once(ctx, self.refresh_credentials if self.oauth else None):
                return await go()
            raise

    async def _get(self, ctx: ConnectorContext, path: str, params: Mapping[str, Any] | None = None, **kw: Any) -> HttpResult:
        return await self._req(ctx, "GET", path, params=params, **kw)

    @staticmethod
    def _rate(ctx: ConnectorContext) -> dict[str, Any]:
        snap = getattr(ctx.http, "rate_snapshot", None)
        return snap() if callable(snap) else {}

    @staticmethod
    def _cap(ctx: ConnectorContext, key: str, default: int) -> int:
        try:
            return max(1, int(ctx.config.get(key) or default))
        except (TypeError, ValueError):
            return default

    def _max_bytes(self, ctx: ConnectorContext) -> int:
        return min(self._cap(ctx, "max_file_bytes", 5_000_000), int(ctx.limits.max_attachment_bytes))

    @staticmethod
    def _page_size(ctx: ConnectorContext) -> int:
        return max(1, min(1000, int(ctx.limits.page_size)))

    @staticmethod
    def _fetched(ctx: ConnectorContext) -> str:
        return iso_utc(ctx.clock())

    @staticmethod
    def _drive_of(source: SourceDescriptor) -> str | None:
        if source.source_type == "shared_drive":
            return source.external_id
        d = source.metadata.get("drive_id")
        return str(d) if d and _ID.match(str(d)) else None

    def _corpus(self, source: SourceDescriptor) -> dict[str, str]:
        drive = self._drive_of(source)
        return {"corpora": "drive", "driveId": drive} if drive else {"corpora": "user"}

    @staticmethod
    def _id(value: Any) -> str:
        s = str(value or "")
        if not _ID.match(s):
            raise PermanentError("invalid Drive id", code="bad_id")
        return s

    def _validate_config(self, ctx: ConnectorContext) -> None:
        cfg = ctx.config
        if cfg.get("principal_map") is not None and not isinstance(cfg.get("principal_map"), dict):
            raise PermanentError("principal_map must be an object", code="bad_config")
        ids = cfg.get("folder_ids")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(x, str) and _ID.match(x) for x in ids)):
            raise PermanentError("folder_ids must be a list of Drive folder ids", code="bad_config")

    # ------------------------------------------------------------------ lifecycle
    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        """The scope Google reported with the token, then ``about`` (identity only)."""
        self._validate_config(ctx)
        granted, warnings = await self.check_scopes(ctx)
        res = await self._get(ctx, "/about", {"fields": "user(displayName,emailAddress,permissionId)"})
        user = (res.json or {}).get("user") if isinstance(res.json, dict) else None
        email = valid_email((user or {}).get("emailAddress"))
        if email is None:
            raise PermanentError("unexpected about response", code="bad_response")
        if not ctx.config.get("folder_ids") and not ctx.config.get("include_my_drive"):
            warnings.append("no folders named and My Drive not included: only shared drives will be offered")
        return ConnectResult(source_account_id=self._namespace(ctx), auth_account_id=email, account_label=email, granted_scopes=granted,
                             warnings=tuple(warnings))

    async def health(self, ctx: ConnectorContext) -> HealthReport:
        try:
            await self._get(ctx, "/about", {"fields": "user(emailAddress)"})
        except AuthRevoked as exc:
            return HealthReport("revoked", {"auth": False, "api_reachable": True}, {}, exc.code)
        except AuthExpired as exc:
            return HealthReport("auth_expired", {"auth": False, "api_reachable": True}, {}, exc.code)
        except RateLimited as exc:
            return HealthReport("rate_limited", {"auth": True, "api_reachable": True}, {"retry_after": exc.retry_after}, exc.code)
        except TransientError as exc:
            return HealthReport("degraded", {"auth": True, "api_reachable": False}, {}, exc.code)
        except ConnectorError as exc:
            return HealthReport("error", {"auth": False, "api_reachable": True}, {}, exc.code)
        return HealthReport("ok", {"auth": True, "api_reachable": True, "scopes": True}, {})

    async def disconnect(self, ctx: ConnectorContext, *, revoke_at_provider: bool) -> None:
        if revoke_at_provider:
            await self.revoke(await ctx.secrets.get())

    # ------------------------------------------------------------------ ACLs
    async def _permissions(self, ctx: ConnectorContext, file_id: str) -> list[dict[str, Any]] | None:
        """Every permission of a file or shared drive (inherited ones included), or ``None`` when Drive does not let this
        account see them (the caller then fails closed)."""
        out: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {"fields": PERM_FIELDS, "supportsAllDrives": "true", "pageSize": 100}
            if token:
                params["pageToken"] = token
            try:
                res = await self._get(ctx, f"/files/{file_id}/permissions", params)
            except (InsufficientScope, ObjectGone):
                return None
            data = res.json if isinstance(res.json, dict) else {}
            out += [p for p in data.get("permissions") or [] if isinstance(p, dict)]
            token = str(data.get("nextPageToken") or "") or None
            if not token:
                return out

    def _acl(self, ctx: ConnectorContext, perms: list[Mapping[str, Any]] | None, *, for_source: bool = False) -> Permissions:
        owner = valid_email(ctx.auth_account_id) or ""
        app = ctx.source_app
        if perms is None:
            return Permissions("private", email_principals(ctx, app, [owner] if owner else []))
        live = [p for p in perms if not p.get("deleted")]
        if any(str(p.get("type")) in ("anyone", "domain") for p in live):
            return Permissions("public")
        users = sorted({e for e in (valid_email(p.get("emailAddress")) for p in live if p.get("type") == "user") if e})
        groups = sorted({e for e in (valid_email(p.get("emailAddress")) for p in live if p.get("type") == "group") if e})
        if not groups and set(users) <= {owner}:
            return Permissions("private", email_principals(ctx, app, users or ([owner] if owner else [])))
        if for_source:
            # a source's group members cannot be listed: with a group grant the source does not narrow by membership
            return Permissions("members", () if groups else email_principals(ctx, app, users))
        ref = "&".join(f"google_drive:group:{g}" for g in groups) or None
        return Permissions("members", email_principals(ctx, app, users), ref)

    # ------------------------------------------------------------------ sources
    def _descriptor(self, ctx: ConnectorContext, kind: str, sid: str, name: str, perms: Permissions, **meta: Any) -> SourceDescriptor:
        drive = meta.get("drive_id")
        return SourceDescriptor(source_type=kind, external_id=sid, name=name[:200], parent_external_id=drive if kind == "folder" else None,
                                visibility=perms.visibility, member_ids=perms.member_ids, metadata={k: v for k, v in meta.items() if v is not None})

    async def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:
        self._validate_config(ctx)
        token: str | None = None
        while True:
            if ctx.cancelled.is_set():
                return
            params: dict[str, Any] = {"pageSize": 100, "fields": "nextPageToken,drives(id,name,hidden)"}
            if token:
                params["pageToken"] = token
            res = await self._get(ctx, "/drives", params)
            data = res.json if isinstance(res.json, dict) else {}
            for d in data.get("drives") or []:
                if isinstance(d, dict) and _ID.match(str(d.get("id") or "")):
                    perms = self._acl(ctx, await self._permissions(ctx, str(d["id"])), for_source=True)
                    yield self._descriptor(ctx, "shared_drive", str(d["id"]), str(d.get("name") or d["id"]), perms, drive_id=str(d["id"]),
                                           hidden=bool(d.get("hidden")))
            token = str(data.get("nextPageToken") or "") or None
            if not token:
                break
        for fid in ctx.config.get("folder_ids") or []:
            meta = await self._file_meta(ctx, fid, fields="id,name,mimeType,driveId,trashed")
            if meta is None or meta.get("mimeType") != FOLDER or meta.get("trashed"):
                continue                                            # gone, not a folder, or in the trash: nothing to offer
            perms = self._acl(ctx, await self._permissions(ctx, fid), for_source=True)
            yield self._descriptor(ctx, "folder", fid, str(meta.get("name") or fid), perms, drive_id=meta.get("driveId"), named=True)
        if ctx.config.get("include_my_drive"):
            root = await self._file_meta(ctx, "root", fields="id,name")
            if root is not None and _ID.match(str(root.get("id") or "")):
                perms = self._acl(ctx, await self._permissions(ctx, str(root["id"])), for_source=True)
                yield self._descriptor(ctx, "folder", str(root["id"]), "My Drive", perms, my_drive=True)

    # ------------------------------------------------------------------ files
    async def _file_meta(self, ctx: ConnectorContext, file_id: str, *, fields: str = FILE_FIELDS) -> dict[str, Any] | None:
        if file_id != "root" and not _ID.match(file_id or ""):
            return None
        try:
            res = await self._get(ctx, f"/files/{file_id}", {"fields": fields, "supportsAllDrives": "true"})
        except ObjectGone:
            return None
        return res.json if isinstance(res.json, dict) else None

    async def _file_item(self, ctx: ConnectorContext, source: SourceDescriptor, f: Mapping[str, Any]) -> RawItem | None:
        """The file's text and permissions as a raw item, or ``None`` when it is not a readable text document."""
        fid, mt = str(f.get("id") or ""), str(f.get("mimeType") or "")
        if not _ID.match(fid) or mt == FOLDER or f.get("trashed"):
            return None
        cap = self._max_bytes(ctx)
        try:
            if mt == GDOC:
                res = await self._get(ctx, f"/files/{fid}/export", {"mimeType": "text/plain"}, max_bytes=cap)
                text = res.body.decode("utf-8", errors="replace")
            elif mt in DOWNLOADABLE:
                size = str(f.get("size") or "")
                if _DIGITS.match(size) and int(size) > cap:
                    return None
                res = await self._get(ctx, f"/files/{fid}", {"alt": "media", "supportsAllDrives": "true"}, max_bytes=cap)
                text, _info = extract_text(DOWNLOADABLE[mt], res.body)
            else:
                return None                                          # images, sheets, slides, binaries: not text documents
        except ObjectGone:
            return None                                              # gone between the listing and the download
        except ExtractError:
            return None
        except PermanentError as exc:
            if exc.code in ("response_too_large", "bad_json"):
                return None
            raise
        text = text.lstrip("﻿")
        if not text.strip():
            return None
        perms = await self._permissions(ctx, fid)
        return RawItem("file", {"file": dict(f), "permissions": perms, "text": text}, source, self._fetched(ctx))

    async def _list_children(self, ctx: ConnectorContext, source: SourceDescriptor, folder: str, token: str | None) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"q": f"'{self._id(folder)}' in parents and trashed = false", "fields": LIST_FIELDS, "pageSize": self._page_size(ctx),
                                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", **self._corpus(source)}
        if token:
            params["pageToken"] = token
        res = await self._get(ctx, "/files", params)
        data = res.json if isinstance(res.json, dict) else {}
        return [f for f in data.get("files") or [] if isinstance(f, dict)], (str(data.get("nextPageToken") or "") or None)

    async def _crawl(self, ctx: ConnectorContext, source: SourceDescriptor, state: dict[str, Any], lo: str,
                     hi: str) -> AsyncIterator[tuple[list[RawItem], dict[str, Any] | None]]:
        """Breadth-first over the source's folders, one ``files.list`` page per step; files with ``modifiedTime`` in
        ``[lo, hi)`` become raw items. Yields ``(items, state)``; ``state`` is ``None`` once the crawl is complete."""
        max_depth, max_folders, max_files = self._cap(ctx, "max_depth", 5), self._cap(ctx, "max_folders", 500), self._cap(ctx, "max_files", 5000)
        if not state["queue"]:
            yield [], None
            return
        while state["queue"]:
            if ctx.cancelled.is_set():
                return
            folder, depth = state["queue"][0]
            try:
                children, token = await self._list_children(ctx, source, folder, state.get("page"))
            except ObjectGone:
                if depth == 0:
                    await self._check_container(ctx, source)         # the source itself unreadable: access lost
                children, token = [], None                           # a subfolder vanished mid-crawl
            items: list[RawItem] = []
            for f in children:
                fid = str(f.get("id") or "")
                if not _ID.match(fid):
                    continue
                if f.get("mimeType") == FOLDER:
                    if depth + 1 <= max_depth and state["folders"] < max_folders:
                        state["queue"].append([fid, depth + 1])
                        state["folders"] += 1
                    continue
                if state["files"] >= max_files or not (lo <= (normalize_ts(f.get("modifiedTime")) or "") < hi):
                    continue
                item = await self._file_item(ctx, source, f)
                if item is not None:
                    items.append(item)
                    state["files"] += 1
            if token:
                state["page"] = token
            else:
                state["queue"].pop(0)
                state["page"] = None
            yield items, (state if state["queue"] else None)

    @staticmethod
    def _new_crawl(source: SourceDescriptor) -> dict[str, Any]:
        return {"queue": [[source.external_id, 0]], "page": None, "folders": 0, "files": 0}

    # ------------------------------------------------------------------ pull
    def plan_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, *, anchor: datetime) -> list[BackfillWindow]:
        """One crawl of the source over ``[anchor − backfill_days, anchor)``: folders do not carry their files' times, so
        windows would repeat the folder walk; the crawl state makes the single window resumable instead."""
        start = iso_utc(anchor - timedelta(days=max(1, ctx.limits.backfill_days)))
        return [BackfillWindow(start=start, end=iso_utc(anchor), stream=f"backfill:{source.external_id}:{start}")]

    async def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,
                               cursor: Cursor | None) -> AsyncIterator[Page]:
        self._id(source.external_id)
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        if data.get("done"):
            return
        lo, hi = normalize_ts(window.start), normalize_ts(window.end)
        if not lo or not hi:
            raise PermanentError("invalid backfill window", code="bad_window")
        state = data.get("crawl") if isinstance(data.get("crawl"), dict) else self._new_crawl(source)
        async for items, st in self._crawl(ctx, source, state, lo, hi):
            if st is not None:
                yield Page(stream=window.stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"crawl": st}), has_more=True, rate=self._rate(ctx))
            else:
                yield Page(stream=window.stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"done": True}, hi), has_more=False, rate=self._rate(ctx))

    async def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:
        """The source's changes since the stored page token; without one, a new start token first and then a catch-up crawl
        of the recent window (see module docstring)."""
        self._id(source.external_id)
        data = dict(cursor.data) if cursor is not None and cursor.version == CURSOR_VERSION else {}
        stream = f"incr:{source.external_id}"
        if data.get("token") and not data.get("crawl"):
            async for page in self._changes(ctx, source, str(data["token"]), stream):
                yield page
            return
        start = str(data.get("start") or "") or await self._start_token(ctx, source)
        lookback = int(ctx.config.get("initial_lookback_days") or ctx.limits.backfill_window_days)
        lo = str(data.get("lo") or iso_utc(ctx.clock() - timedelta(days=max(1, lookback))))
        state = data.get("crawl") if isinstance(data.get("crawl"), dict) else self._new_crawl(source)
        async for items, st in self._crawl(ctx, source, state, normalize_ts(lo) or lo, _FAR_FUTURE):
            if st is not None:
                yield Page(stream=stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"start": start, "lo": lo, "crawl": st}), has_more=True,
                           rate=self._rate(ctx))
            else:
                yield Page(stream=stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"token": start}, iso_utc(ctx.clock())), has_more=False,
                           rate=self._rate(ctx))

    async def _start_token(self, ctx: ConnectorContext, source: SourceDescriptor) -> str:
        params: dict[str, Any] = {"supportsAllDrives": "true"}
        drive = self._drive_of(source)
        if drive:
            params["driveId"] = drive
        try:
            res = await self._get(ctx, "/changes/startPageToken", params)
        except ObjectGone:
            await self._check_container(ctx, source)
            raise
        token = str((res.json or {}).get("startPageToken") or "") if isinstance(res.json, dict) else ""
        if not token or len(token) > 512:
            raise PermanentError("no start page token", code="bad_response")
        return token

    async def _check_container(self, ctx: ConnectorContext, source: SourceDescriptor) -> None:
        """The source itself must answer before anything inside it is called deleted."""
        try:
            if source.source_type == "shared_drive":
                await self._get(ctx, f"/drives/{self._id(source.external_id)}", {"fields": "id"})
                return
            res = await self._get(ctx, f"/files/{self._id(source.external_id)}", {"fields": "id,trashed", "supportsAllDrives": "true"})
        except (ObjectGone, InsufficientScope):
            raise SourceUnavailable("access_lost", detail={"source_type": source.source_type}) from None
        if isinstance(res.json, dict) and res.json.get("trashed"):
            raise SourceUnavailable("deleted", detail={"source_type": source.source_type})

    async def _locate(self, ctx: ConnectorContext, source: SourceDescriptor, f: Mapping[str, Any],
                      cache: dict[str, list[str]]) -> tuple[bool, list[str]]:
        """``(inside the source, ancestor ids)``: the parent chain walked upwards (cached per pass) plus the shared drive."""
        root, drive = source.external_id, str(f.get("driveId") or "") or None
        if source.source_type == "shared_drive" and drive == root:
            return True, [root]
        chain: list[str] = []
        parents = [str(p) for p in f.get("parents") or []]
        cur = parents[0] if parents else None
        inside = False
        max_depth = self._cap(ctx, "max_depth", 5)
        while cur and _ID.match(cur) and len(chain) < MAX_ANCESTRY and cur not in chain:
            chain.append(cur)
            if cur == root:
                inside = source.source_type == "folder" and len(chain) - 1 <= max_depth
                break
            if cur not in cache:
                meta = await self._file_meta(ctx, cur, fields="id,parents,driveId")
                cache[cur] = [str(p) for p in (meta or {}).get("parents") or []]
            nxt = cache[cur]
            cur = nxt[0] if nxt else None
        return inside, chain + ([drive] if drive and drive not in chain else [])

    def _deletion(self, ctx: ConnectorContext, source: SourceDescriptor, fid: str, reason: str, **extra: Any) -> RawItem:
        return RawItem("deletion", {"id": fid, "reason": reason, **extra}, source, self._fetched(ctx))

    async def _folder_change(self, ctx: ConnectorContext, source: SourceDescriptor, folder: Mapping[str, Any], inside: bool,
                             ancestors: list[str]) -> list[RawItem]:
        """A folder moved (or renamed): read its files when it is inside the source, let them leave it when it is not."""
        budget = self._cap(ctx, "max_files_per_change", 500)
        out: list[RawItem] = []
        queue: list[tuple[str, list[str]]] = [(str(folder["id"]), [str(folder["id"]), *ancestors])]
        seen: set[str] = set()
        while queue and budget > 0:
            fid, chain = queue.pop(0)
            if fid in seen or len(chain) > MAX_ANCESTRY:
                continue
            seen.add(fid)
            token: str | None = None
            while budget > 0:
                try:
                    children, token = await self._list_children(ctx, source, fid, token)
                except ObjectGone:
                    break
                for f in children:
                    cid = str(f.get("id") or "")
                    if not _ID.match(cid):
                        continue
                    if f.get("mimeType") == FOLDER:
                        queue.append((cid, [cid, *chain]))
                    elif inside:
                        item = await self._file_item(ctx, source, f)
                        if item is not None:
                            out.append(item)
                            budget -= 1
                    else:
                        out.append(self._deletion(ctx, source, cid, "excluded_source", still_in=chain))
                        budget -= 1
                if not token:
                    break
        return out

    async def _changes(self, ctx: ConnectorContext, source: SourceDescriptor, token: str, stream: str) -> AsyncIterator[Page]:
        drive = self._drive_of(source)
        cache: dict[str, list[str]] = {}
        container_ok = False
        while True:
            if ctx.cancelled.is_set():
                return
            params: dict[str, Any] = {"pageToken": token, "pageSize": self._page_size(ctx), "fields": CHANGE_FIELDS, "supportsAllDrives": "true",
                                      "includeItemsFromAllDrives": "true", "includeRemoved": "true", "spaces": "drive"}
            if drive:
                params["driveId"] = drive
            res = await self._get(ctx, "/changes", params, expected=(200, 400, 404, 410))
            if res.status != 200:
                await self._check_container(ctx, source)          # a drive this account left also answers 404: access lost, not a stale token
                raise CursorInvalid("Drive refused the stored page token", detail={"status": res.status})
            body = res.json if isinstance(res.json, dict) else {}
            items: list[RawItem] = []
            for ch in body.get("changes") or []:
                if not isinstance(ch, dict) or ch.get("changeType", "file") != "file":
                    continue                                       # shared-drive metadata changes: discovery covers them
                fid = str(ch.get("fileId") or "")
                if not _ID.match(fid):
                    continue
                f = ch.get("file") if isinstance(ch.get("file"), dict) else None
                if ch.get("removed") or f is None:
                    f = await self._file_meta(ctx, fid)
                    if f is None:
                        if not container_ok:
                            await self._check_container(ctx, source)
                            container_ok = True
                        items.append(self._deletion(ctx, source, fid, "deleted_at_source"))
                        continue
                if f.get("trashed"):
                    items.append(self._deletion(ctx, source, fid, "deleted_at_source", trashed=True))
                    continue
                inside, ancestors = await self._locate(ctx, source, f, cache)
                if f.get("mimeType") == FOLDER:
                    items += await self._folder_change(ctx, source, f, inside, ancestors)
                elif inside:
                    item = await self._file_item(ctx, source, f)
                    if item is not None:
                        items.append(item)
                else:
                    items.append(self._deletion(ctx, source, fid, "excluded_source", still_in=ancestors))
            nxt = str(body.get("nextPageToken") or "") or None
            if nxt:
                yield Page(stream=stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"token": nxt}), has_more=True, rate=self._rate(ctx))
                token = nxt
                continue
            new = str(body.get("newStartPageToken") or "") or token
            yield Page(stream=stream, items=items, next_cursor=Cursor(CURSOR_VERSION, {"token": new}, iso_utc(ctx.clock())), has_more=False,
                       rate=self._rate(ctx))
            return

    # ------------------------------------------------------------------ push (changes.watch)
    async def watch_changes(self, ctx: ConnectorContext, source: SourceDescriptor, *, address: str, token: str, channel_id: str,
                            ttl_seconds: int = 86400) -> dict[str, str]:
        """Open a ``changes.watch`` channel for one source, delivering to ``address`` (the webhook endpoint) with ``token``
        (its secret, echoed in ``X-Goog-Channel-Token``). Returns the channel's ids and expiry (no content)."""
        if not _CHANNEL.match(channel_id) or not address.startswith("https://") and not address.startswith("http://127.0.0.1"):
            raise PermanentError("invalid channel id or address", code="bad_request")
        params: dict[str, Any] = {"pageToken": await self._start_token(ctx, source), "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        drive = self._drive_of(source)
        if drive:
            params["driveId"] = drive
        expiration = int((ctx.clock() + timedelta(seconds=max(60, ttl_seconds))).timestamp() * 1000)
        res = await self._req(ctx, "POST", "/changes/watch", params=params,
                              json={"id": channel_id, "type": "web_hook", "address": address, "token": token, "expiration": str(expiration)})
        data = res.json if isinstance(res.json, dict) else {}
        return {"id": str(data.get("id") or ""), "resourceId": str(data.get("resourceId") or ""), "expiration": str(data.get("expiration") or "")}

    async def stop_channel(self, ctx: ConnectorContext, *, channel_id: str, resource_id: str) -> None:
        await self._req(ctx, "POST", "/channels/stop", json={"id": channel_id, "resourceId": resource_id}, expected=(200, 204, 404))

    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        """``X-Goog-Channel-Token`` equals the channel's secret (constant-time), on a well-formed channel notification. Drive
        signs nothing else; replays are stopped by the ``<channel>:<message number>`` dedupe."""
        token = header(headers, "x-goog-channel-token")
        if not token or not secret or not _CHANNEL.match(header(headers, "x-goog-channel-id")) or not header(headers, "x-goog-resource-state"):
            return False
        return hmac.compare_digest(token.encode("utf-8", errors="replace"), bytes(secret))

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        """Ids only, from the headers (the body of a changes notification is empty): ``sync`` (the handshake after a watch)
        yields nothing; any other state is one account-wide notice that triggers a changes fetch."""
        channel, state = header(headers, "x-goog-channel-id"), header(headers, "x-goog-resource-state").lower()
        number, resource = header(headers, "x-goog-message-number"), header(headers, "x-goog-resource-id")
        if not _CHANNEL.match(channel) or not _DIGITS.match(number) or state in ("", "sync"):
            return []
        if state not in ("change", "update", "add", "remove", "trash", "untrash", "exists", "not_exists"):
            return []
        ref = {"type": "channel", "id": channel}
        if resource and _CHANNEL.match(resource):
            ref["resource_id"] = resource
        return [WebhookNotice(connector_type=cls.manifest.connector_type, delivery_id=f"{channel}:{number}", external_account_id="",
                              source_external_id="*", action=f"changes.{state}", object_refs=(ref,), occurred_at=None)]

    # ------------------------------------------------------------------ normalize (pure)
    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list[CanonicalEvent]:
        p = raw.payload
        if not isinstance(p, dict):
            raise PermanentError("invalid Drive item", code="normalize_failed")
        common: dict[str, Any] = dict(tenant_id=ctx.tenant_id, holder_id=ctx.holder_id, connector_id=ctx.connector_id, source_app=ctx.source_app,
                                      source_account_id=ctx.source_account_id, observed_at=raw.fetched_at)
        try:
            if raw.object_type == "deletion":
                fid = self._id(p["id"])
                hints: dict[str, Any] = {"reason": str(p.get("reason") or "deleted_at_source"), "if_known": True,
                                         "still_in": [str(x) for x in p.get("still_in") or []]}
                if p.get("trashed"):
                    hints["trashed"] = True
                return [CanonicalEvent.create(kind="deletion", source_object_type="drive_file", source_object_id=fid, updated_at=raw.fetched_at,
                                              tiebreak="deleted", permissions=Permissions("private"), hints=hints, **common)]
            if raw.object_type == "file":
                return self._file_events(p, raw, ctx, common)
        except (KeyError, TypeError, ValueError):
            raise PermanentError("malformed Drive item", code="normalize_failed", detail={"object_type": raw.object_type}) from None
        raise PermanentError("unknown Drive item type", code="normalize_failed", detail={"object_type": raw.object_type})

    def _file_events(self, p: Mapping[str, Any], raw: RawItem, ctx: ConnectorContext, common: dict[str, Any]) -> list[CanonicalEvent]:
        f = p["file"]
        fid = self._id(f["id"])
        text = str(p.get("text") or "")
        name = str(f.get("name") or fid)
        mt = str(f.get("mimeType") or "")
        version = str(f.get("version") or "")
        owners = [e for e in (valid_email((o or {}).get("emailAddress")) for o in f.get("owners") or [] if isinstance(o, dict)) if e]
        editor = valid_email((f.get("lastModifyingUser") or {}).get("emailAddress")) if isinstance(f.get("lastModifyingUser"), dict) else None
        author = editor or (owners[0] if owners else None)
        src = raw.source
        hints = {"container_name": src.name, "container_kind": src.source_type, "labels": [], "mime_type": mt, "drive_id": f.get("driveId"),
                 "parents": [str(x) for x in f.get("parents") or []][:4]}
        return [CanonicalEvent.create(kind="document", source_object_type="drive_file", source_object_id=fid, source_version=version, author_id=author,
                                      participant_ids=list(dict.fromkeys([*owners, *([editor] if editor else [])])), created_at=f.get("createdTime"),
                                      updated_at=f.get("modifiedTime"), title=name, body=text,
                                      content_type="text/markdown" if mt in ("text/markdown", "text/x-markdown") else "text/plain",
                                      permissions=self._acl(ctx, p.get("permissions")), hints=hints, links=extract_urls(text),
                                      tiebreak=f"{int(version):020d}" if _DIGITS.match(version) else None, **common)]


__all__ = ["GoogleDriveConnector", "READONLY"]

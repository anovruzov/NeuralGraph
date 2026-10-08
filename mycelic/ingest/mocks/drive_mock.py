"""A faithful offline Google Drive API v3 (files, permissions, shared drives, changes, push channels) for tests and demos.

Start it with :func:`start_drive_mock`::

    url, dm = await start_drive_mock(load_fixture("drive_acme"), clock=FakeClock(...))
    # connector config: {"api_base": url, "folder_ids": [...]}; OAuth: OAuthAppConfig(client_id, Secret(secret), oauth_base=url,
    # api_base=url, allow_loopback_http=True); a push: headers = dm.edit_file(...)[0] (one per open channel)
    await dm.close()

Modelled behaviour (Drive API v3 reference as understood when this was written; re-verify against developers.google.com):

* ``/drive/v3/``: ``about`` (``fields`` required), ``drives`` and ``drives/{id}`` (members only), ``files`` (``q`` with
  ``'<id>' in parents``, ``trashed``, ``mimeType``, ``modifiedTime`` and ``name`` terms joined by ``and``; ``corpora``
  ``user|drive|allDrives``; ``driveId`` needs ``includeItemsFromAllDrives=true``; shared-drive items appear only with
  ``supportsAllDrives=true``; ``pageSize`` default 100, at most 1000; opaque ``pageToken``), ``files/{id}`` (``root`` alias,
  a shared drive's id is its root folder; ``alt=media`` downloads binary content, and refuses Google Docs with 403
  ``fileNotDownloadable``), ``files/{id}/export`` (Google Docs only, ``text/plain`` with a byte-order mark as Drive sends it;
  other files 403 ``fileNotExportable``), ``files/{id}/permissions`` (inherited permissions included, ``permissionDetails``
  on shared-drive items; 403 ``insufficientFilePermissions`` where the fixture hides them), ``changes/startPageToken``,
  ``changes`` (the *current* state of each changed file, ``removed`` when it is deleted or no longer readable,
  ``nextPageToken`` or ``newStartPageToken``; an expired page token answers 404), ``changes/watch`` and ``channels/stop``.
* Partial responses: ``fields`` masks are applied (``nextPageToken,files(id,name,owners(emailAddress))``); without one,
  Drive's small defaults (``kind,id,name,mimeType``) come back, so a connector that forgets ``fields`` sees what it would see.
* Access: an account reads its My Drive, files shared with it (as a user, through a group, its domain, or anyone), and
  shared drives it is a member of (files inherit the drive's members unless ``inheritedPermissionsDisabled``); My Drive
  files inherit their folders' permissions.
* Faults: ``rate_limit_next`` (403 ``userRateLimitExceeded`` / ``rateLimitExceeded``, 429), ``revoke_token``,
  ``expire_token``, ``fail_next``, ``crash_after_pages``, :meth:`expire_changes`.
* Mutations (:meth:`edit_file`, :meth:`set_permissions`, :meth:`move_file`, :meth:`trash_file`, :meth:`delete_file`,
  :meth:`add_file`) record a change and return the notification headers Drive would POST to each open channel
  (``X-Goog-Channel-ID``, ``X-Goog-Channel-Token``, ``X-Goog-Resource-State: change``, ``X-Goog-Message-Number`` ...).
* OAuth: Google's ``/o/oauth2/auth`` and ``/token`` with PKCE, and ``/revoke``.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import re
import secrets
import zipfile
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Callable, Iterable, Mapping

import aiohttp
from aiohttp import web

from .common import iso_z, load_fixture, parse_iso
from .google_common import GoogleMockBase, apply_mask, field_mask

FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
READ_SCOPES = frozenset({"https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive"})
ROLE_RANK = {"reader": 0, "commenter": 1, "writer": 2, "fileOrganizer": 3, "organizer": 4, "owner": 5}
_TERM_PARENT = re.compile(r"^'([^']+)'\s+in\s+parents$")
_TERM_TRASHED = re.compile(r"^trashed\s*=\s*(true|false)$")
_TERM_MIME = re.compile(r"^mimeType\s*(=|!=)\s*'([^']+)'$")
_TERM_TIME = re.compile(r"^modifiedTime\s*(>=|<=|>|<|=)\s*'([^']+)'$")
_TERM_NAME = re.compile(r"^name\s*(=|contains)\s*'([^']*)'$")


async def start_drive_mock(fixture: Mapping[str, Any] | None = None, *, clock: Callable[[], float] | None = None, latency: float = 0.0,
                           host: str = "127.0.0.1", port: int = 0) -> tuple[str, "DriveMock"]:
    """Start a mock on loopback; returns ``(base_url, controller)``. ``fixture`` defaults to ``drive_acme``."""
    mock = DriveMock(fixture if fixture is not None else load_fixture("drive_acme"), clock=clock, latency=latency)
    return await mock.start(host, port), mock


# ---------------------------------------------------------------------------------------------- binary fixtures
def pdf_bytes(text: str) -> bytes:
    """A small, valid one-page PDF (Helvetica, one line per text line) that ``pypdf`` extracts text from."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"] + [f"({esc(line)}) Tj T*" for line in text.split("\n")] + ["ET"]
    content = "\n".join(ops).encode("latin-1", errors="replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def docx_bytes(text: str) -> bytes:
    """A minimal valid DOCX (one paragraph per text line)."""
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paras = "".join(f'<w:p><w:r><w:t xml:space="preserve">{esc(line)}</w:t></w:r></w:p>' for line in text.split("\n"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{paras}</w:body></w:document>')
    return buf.getvalue()


def _token(offset: int, kind: str) -> str:
    return base64.urlsafe_b64encode(f"{kind}:{offset}".encode()).decode().rstrip("=")


def _offset(token: str | None, kind: str) -> int | None:
    if not token:
        return 0
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except Exception:
        return None
    head, _, num = raw.partition(":")
    return int(num) if head == kind and num.isdigit() else None


class DriveMock(GoogleMockBase):
    name = "drive"
    read_scopes = READ_SCOPES | {"https://www.googleapis.com/auth/drive.metadata.readonly"}

    def __init__(self, fixture: Mapping[str, Any], *, clock: Callable[[], float] | None = None, latency: float = 0.0) -> None:
        fx = copy.deepcopy(dict(fixture))
        self.domain = str(fx.get("domain") or "acme.example")
        self.users: dict[str, dict[str, Any]] = {u["email"].lower(): dict(u) for u in fx.get("users") or []}
        self.groups: dict[str, list[str]] = {g.lower(): [m.lower() for m in ms] for g, ms in (fx.get("groups") or {}).items()}
        self.drives: dict[str, dict[str, Any]] = {d["id"]: dict(d) for d in fx.get("drives") or []}
        self.files: dict[str, dict[str, Any]] = {}
        for u in self.users.values():
            if u.get("root"):
                self.files[u["root"]] = {"id": u["root"], "name": "My Drive", "mimeType": FOLDER, "parents": [], "owner": u["email"].lower(),
                                         "createdTime": "2020-01-01T00:00:00Z", "modifiedTime": "2020-01-01T00:00:00Z", "version": 1, "is_root": True}
        for f in fx.get("files") or []:
            d = dict(f)
            d.setdefault("parents", [])
            d.setdefault("version", 10)
            d.setdefault("permissions", [])
            if d.get("owner"):
                d["owner"] = d["owner"].lower()
            self.files[d["id"]] = d
        self.changes: list[dict[str, Any]] = []
        self.next_change = int(fx.get("change_start") or 4100)
        self.oldest_change = self.next_change
        self.channels: dict[str, dict[str, Any]] = {}
        self._seq = 0
        super().__init__(fx, clock=clock, latency=latency)

    # ------------------------------------------------------------------ routes
    def _api_routes(self, app: web.Application) -> None:
        r = app.router
        p = "/drive/v3"
        r.add_get(p + "/about", self.h_about)
        r.add_get(p + "/drives", self.h_drives)
        r.add_get(p + "/drives/{drive_id}", self.h_drive)
        r.add_get(p + "/files", self.h_files)
        r.add_get(p + "/files/{file_id}", self.h_file)
        r.add_get(p + "/files/{file_id}/export", self.h_export)
        r.add_get(p + "/files/{file_id}/permissions", self.h_permissions)
        r.add_get(p + "/changes/startPageToken", self.h_start_token)
        r.add_get(p + "/changes", self.h_changes)
        r.add_post(p + "/changes/watch", self.h_watch)
        r.add_post(p + "/channels/stop", self.h_stop)

    # ------------------------------------------------------------------ access model
    def _user_of(self, info: Mapping[str, Any]) -> str:
        return str(info.get("user") or "").lower()

    def _member_role(self, user: str, drive_id: str) -> str | None:
        d = self.drives.get(drive_id)
        if d is None:
            return None
        best = None
        for m in d.get("members") or []:
            who = str(m.get("email") or "").lower()
            if who == user or user in self.groups.get(who, []):
                if best is None or ROLE_RANK[m["role"]] > ROLE_RANK[best]:
                    best = m["role"]
        return best

    def _ancestors(self, f: Mapping[str, Any]) -> list[dict[str, Any]]:
        out, seen = [], set()
        cur = f
        while cur.get("parents"):
            pid = cur["parents"][0]
            if pid in seen or pid not in self.files:
                break
            seen.add(pid)
            cur = self.files[pid]
            out.append(cur)
        return out

    def effective_permissions(self, f: Mapping[str, Any]) -> list[dict[str, Any]]:
        """``(type, role, email/domain, inherited, from)`` entries: drive members or the owner, inherited folder grants,
        then the file's own, the highest role kept per grantee."""
        out: list[dict[str, Any]] = []
        drive = f.get("driveId")
        if drive:
            if not f.get("inheritedPermissionsDisabled"):
                for m in self.drives.get(drive, {}).get("members") or []:
                    who = str(m["email"]).lower()
                    out.append({"type": "group" if who in self.groups else "user", "role": m["role"], "emailAddress": who, "inherited": True,
                                "from": drive, "detail": "member"})
        else:
            owner = f.get("owner")
            if owner:
                out.append({"type": "user", "role": "owner", "emailAddress": owner, "inherited": False})
            if not f.get("inheritedPermissionsDisabled"):
                for a in self._ancestors(f):
                    if a.get("owner"):              # the owner of a folder sees what is put in it
                        out.append({"type": "user", "role": "writer", "emailAddress": a["owner"], "inherited": True, "from": a["id"]})
                    for p in a.get("permissions") or []:
                        out.append({**p, "inherited": True, "from": a["id"]})
        out += [{**p, "inherited": False} for p in f.get("permissions") or []]
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for p in out:
            key = (p["type"], str(p.get("emailAddress") or p.get("domain") or "anyone").lower())
            if key not in best or ROLE_RANK.get(p["role"], 0) > ROLE_RANK.get(best[key]["role"], 0):
                best[key] = p
        return list(best.values())

    def can_read(self, user: str, f: Mapping[str, Any]) -> bool:
        if f.get("deleted"):
            return False
        if f.get("is_root"):
            return f.get("owner") == user
        for p in self.effective_permissions(f):
            t = p["type"]
            if t == "anyone":
                return True
            if t == "domain" and str(p.get("domain") or "").lower() == user.rsplit("@", 1)[-1]:
                return True
            who = str(p.get("emailAddress") or "").lower()
            if t == "user" and who == user:
                return True
            if t == "group" and user in self.groups.get(who, []):
                return True
        return False

    def readers(self, f: Mapping[str, Any]) -> set[str]:
        return {u for u in self.users if self.can_read(u, f)}

    # ------------------------------------------------------------------ JSON
    def user_json(self, email: str, viewer: str) -> dict[str, Any]:
        u = self.users.get(email.lower()) or {"email": email, "name": email.split("@")[0], "permissionId": "0" + hashlib.sha1(email.encode()).hexdigest()[:18]}
        return {"kind": "drive#user", "displayName": u.get("name") or email, "emailAddress": u["email"], "permissionId": u.get("permissionId"),
                "me": u["email"].lower() == viewer, "photoLink": "https://lh3.googleusercontent.com/a/mock=s64"}

    def content(self, f: Mapping[str, Any]) -> bytes:
        kind = f.get("content_kind") or "text"
        text = str(f.get("text") or "")
        if kind == "pdf":
            return pdf_bytes(text)
        if kind == "docx":
            return docx_bytes(text)
        if kind == "binary":
            return b"\x89PNG\r\n\x1a\n" + b"\0" * int(f.get("size") or 1024)
        return text.encode("utf-8")

    def file_json(self, f: Mapping[str, Any], viewer: str) -> dict[str, Any]:
        mt = f["mimeType"]
        d: dict[str, Any] = {"kind": "drive#file", "id": f["id"], "name": f["name"], "mimeType": mt, "parents": list(f.get("parents") or []),
                             "createdTime": f.get("createdTime"), "modifiedTime": f.get("modifiedTime"), "version": str(f.get("version") or 1),
                             "trashed": bool(f.get("trashed")), "explicitlyTrashed": bool(f.get("explicitly_trashed")), "starred": False,
                             "viewedByMe": True, "shared": len(self.effective_permissions(f)) > 1,
                             "webViewLink": (f"https://docs.google.com/document/d/{f['id']}/edit" if mt == GDOC else f"https://drive.google.com/file/d/{f['id']}/view"),
                             "capabilities": {"canDownload": True, "canEdit": False, "canListChildren": mt == FOLDER, "canShare": False},
                             "lastModifyingUser": self.user_json(f.get("lastModifyingUser") or f.get("owner") or viewer, viewer)}
        if f.get("driveId"):
            d["driveId"] = f["driveId"]
            d["teamDriveId"] = f["driveId"]
        elif f.get("owner"):
            d["owners"] = [self.user_json(f["owner"], viewer)]
            d["ownedByMe"] = f["owner"] == viewer
        if f.get("inheritedPermissionsDisabled"):
            d["inheritedPermissionsDisabled"] = True
        if mt not in (FOLDER, GDOC):
            raw = self.content(f)
            d.update({"size": str(len(raw)), "md5Checksum": hashlib.md5(raw).hexdigest(), "sha256Checksum": hashlib.sha256(raw).hexdigest()})
        return d

    def permission_json(self, p: Mapping[str, Any], *, shared_drive: bool) -> dict[str, Any]:
        t = p["type"]
        key = str(p.get("emailAddress") or p.get("domain") or "anyone").lower()
        pid = "anyoneWithLink" if t == "anyone" else (self.users.get(key, {}).get("permissionId") or "0" + hashlib.sha1(key.encode()).hexdigest()[:18])
        d: dict[str, Any] = {"kind": "drive#permission", "id": pid, "type": t, "role": p["role"], "deleted": False}
        if t in ("user", "group"):
            d["emailAddress"] = key
            d["displayName"] = (self.users.get(key) or {}).get("name") or key
        if t == "domain":
            d["domain"] = p.get("domain")
        if t in ("anyone", "domain"):
            d["allowFileDiscovery"] = bool(p.get("allowFileDiscovery"))
        if shared_drive:
            d["permissionDetails"] = [{"permissionType": "member" if p.get("detail") == "member" else "file", "role": p["role"],
                                       "inherited": bool(p.get("inherited")), **({"inheritedFrom": p["from"]} if p.get("from") else {})}]
        return d

    def drive_json(self, d: Mapping[str, Any]) -> dict[str, Any]:
        return {"kind": "drive#drive", "id": d["id"], "name": d["name"], "createdTime": d.get("createdTime") or "2024-01-15T09:00:00Z", "hidden": False,
                "colorRgb": "#4986e7", "restrictions": {"domainUsersOnly": True, "driveMembersOnly": False}}

    def _respond(self, request: web.Request, data: Any, default: str) -> web.Response:
        return web.json_response(apply_mask(data, field_mask(request.query.get("fields") or default)))

    def _not_found(self, fid: str) -> web.Response:
        return self.error(404, f"File not found: {fid[:60]}.", "notFound", location="fileId", locationType="parameter")

    # ------------------------------------------------------------------ lookup
    def _resolve(self, fid: str, user: str, *, all_drives: bool) -> dict[str, Any] | None:
        """The file (or a shared drive's root folder, or the user's My Drive root) this user can read, else ``None``."""
        if fid == "root":
            fid = self.users.get(user, {}).get("root") or ""
        if fid in self.drives:
            if not all_drives or self._member_role(user, fid) is None:
                return None
            d = self.drives[fid]
            return {"id": fid, "name": d["name"], "mimeType": FOLDER, "parents": [], "driveId": fid, "createdTime": d.get("createdTime"),
                    "modifiedTime": d.get("createdTime"), "version": 1, "is_drive_root": True}
        f = self.files.get(fid)
        if f is None or not self.can_read(user, f):
            return None
        if f.get("driveId") and not all_drives:
            return None                                  # shared-drive items need supportsAllDrives=true
        return f

    # ------------------------------------------------------------------ handlers
    async def h_about(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        if not request.query.get("fields"):
            return self.error(400, "The 'fields' parameter is required for this method.", "required", location="fields", locationType="parameter")
        user = self._user_of(info)
        return self._respond(request, {"kind": "drive#about", "user": self.user_json(user, user),
                                       "storageQuota": {"limit": "16106127360", "usage": "1048576"}}, "user")

    async def h_drives(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        user = self._user_of(info)
        mine = [d for d in self.drives.values() if self._member_role(user, d["id"]) is not None]
        mine.sort(key=lambda d: d["name"])
        try:
            size = max(1, min(100, int(request.query.get("pageSize") or 10)))
        except ValueError:
            return self.error(400, "Invalid value for pageSize", "invalid")
        off = _offset(request.query.get("pageToken"), "drives")
        if off is None:
            return self.error(400, "Invalid Value", "invalid", location="pageToken", locationType="parameter")
        out: dict[str, Any] = {"kind": "drive#driveList", "drives": [self.drive_json(d) for d in mine[off: off + size]]}
        if off + size < len(mine):
            out["nextPageToken"] = _token(off + size, "drives")
        return self._respond(request, out, "kind,nextPageToken,drives(kind,id,name)")

    async def h_drive(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        did = request.match_info["drive_id"]
        if did not in self.drives or self._member_role(self._user_of(info), did) is None:
            return self.error(404, f"Shared drive not found: {did[:60]}", "notFound", location="driveId", locationType="parameter")
        return self._respond(request, self.drive_json(self.drives[did]), "kind,id,name")

    def _predicates(self, q: str) -> list[Callable[[Mapping[str, Any]], bool]] | None:
        preds: list[Callable[[Mapping[str, Any]], bool]] = []
        if not q.strip():
            return preds
        if "(" in q or re.search(r"\s+or\s+", q, re.IGNORECASE):
            return None                                              # this mock understands conjunctions only
        for term in re.split(r"\s+and\s+", q.strip(), flags=re.IGNORECASE):
            term = term.strip()
            if m := _TERM_PARENT.match(term):
                pid = m.group(1)
                preds.append(lambda f, pid=pid: pid in (f.get("parents") or []))
            elif m := _TERM_TRASHED.match(term):
                want = m.group(1) == "true"
                preds.append(lambda f, want=want: bool(f.get("trashed")) == want)
            elif m := _TERM_MIME.match(term):
                op, mt = m.groups()
                preds.append(lambda f, op=op, mt=mt: (f["mimeType"] == mt) == (op == "="))
            elif m := _TERM_TIME.match(term):
                op, val = m.groups()
                bound = parse_iso(val)
                cmp = {">": lambda a: a > bound, ">=": lambda a: a >= bound, "<": lambda a: a < bound, "<=": lambda a: a <= bound, "=": lambda a: a == bound}[op]
                preds.append(lambda f, cmp=cmp: cmp(parse_iso(f.get("modifiedTime") or "1970-01-01T00:00:00Z")))
            elif m := _TERM_NAME.match(term):
                op, val = m.groups()
                preds.append(lambda f, op=op, val=val: f["name"] == val if op == "=" else val.lower() in f["name"].lower())
            else:
                return None
        return preds

    async def h_files(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        user, q = self._user_of(info), request.query
        all_drives = q.get("supportsAllDrives") == "true"
        include_all = q.get("includeItemsFromAllDrives") == "true"
        corpora, drive = q.get("corpora") or "user", q.get("driveId")
        if drive and not (include_all and all_drives):
            return self.error(400, "The includeItemsFromAllDrives parameter must be set to true when driveId is set.", "badRequest")
        if corpora == "drive" and not drive:
            return self.error(400, "The driveId parameter must be specified if and only if corpora is set to drive.", "badRequest")
        if drive and self._member_role(user, drive) is None:
            return self.error(404, f"Shared drive not found: {drive[:60]}", "notFound", location="driveId", locationType="parameter")
        preds = self._predicates(q.get("q") or "")
        if preds is None:
            return self.error(400, "Invalid Value", "invalid", location="q", locationType="parameter")
        try:
            size = max(1, min(1000, int(q.get("pageSize") or 100)))
        except ValueError:
            return self.error(400, "Invalid value for pageSize", "invalid")
        items = []
        for f in self.files.values():
            if f.get("deleted") or f.get("is_root") or not self.can_read(user, f):
                continue
            fd = f.get("driveId")
            if corpora == "drive" and fd != drive:
                continue
            if corpora in ("user", "allDrives") and fd and not (include_all and all_drives):
                continue
            if all(p(f) for p in preds):
                items.append(f)
        items.sort(key=lambda f: (f["mimeType"] != FOLDER, f["name"].lower(), f["id"]))
        kind = "files" + hashlib.sha1((q.get("q") or "").encode()).hexdigest()[:8]
        off = _offset(q.get("pageToken"), kind)
        if off is None:
            return self.error(400, "Invalid Value", "invalid", location="pageToken", locationType="parameter")
        out: dict[str, Any] = {"kind": "drive#fileList", "incompleteSearch": False, "files": [self.file_json(f, user) for f in items[off: off + size]]}
        if off + size < len(items):
            out["nextPageToken"] = _token(off + size, kind)
        return self._respond(request, out, "kind,incompleteSearch,nextPageToken,files(kind,id,name,mimeType)")

    async def h_file(self, request: web.Request) -> web.StreamResponse:
        info, err = self.authenticate(request)
        if err:
            return err
        user, fid = self._user_of(info), request.match_info["file_id"]
        f = self._resolve(fid, user, all_drives=request.query.get("supportsAllDrives") == "true")
        if f is None:
            return self._not_found(fid)
        if request.query.get("alt") == "media":
            if f["mimeType"].startswith("application/vnd.google-apps."):
                return self.error(403, "Only files with binary content can be downloaded. Use Export with Docs Editors files.", "fileNotDownloadable",
                                  location="alt", locationType="parameter")
            return web.Response(body=self.content(f), content_type=f["mimeType"])
        return self._respond(request, self.file_json(f, user), "kind,id,name,mimeType")

    async def h_export(self, request: web.Request) -> web.StreamResponse:
        info, err = self.authenticate(request)
        if err:
            return err
        user, fid = self._user_of(info), request.match_info["file_id"]
        f = self._resolve(fid, user, all_drives=True)
        if f is None:
            return self._not_found(fid)
        target = request.query.get("mimeType") or ""
        if not target:
            return self.error(400, "Required parameter: mimeType", "required", location="mimeType", locationType="parameter")
        if f["mimeType"] != GDOC:
            return self.error(403, "Export only supports Docs Editors files.", "fileNotExportable")
        if target != "text/plain":
            return self.error(400, "The requested conversion is not supported.", "badRequest")
        return web.Response(body=("﻿" + str(f.get("text") or "")).encode("utf-8"), content_type="text/plain", charset="utf-8")

    async def h_permissions(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        user, fid = self._user_of(info), request.match_info["file_id"]
        all_drives = request.query.get("supportsAllDrives") == "true"
        if fid in self.drives:
            if not all_drives or self._member_role(user, fid) is None:
                return self._not_found(fid)
            perms = [{"type": "group" if str(m["email"]).lower() in self.groups else "user", "role": m["role"], "emailAddress": str(m["email"]).lower(),
                      "inherited": False, "detail": "member"} for m in self.drives[fid].get("members") or []]
            shared = True
        else:
            f = self._resolve(fid, user, all_drives=all_drives)
            if f is None:
                return self._not_found(fid)
            if f.get("permissions_hidden"):
                return self.error(403, "The user does not have sufficient permissions for this file.", "insufficientFilePermissions")
            perms = self.effective_permissions(f)
            shared = bool(f.get("driveId"))
        out = {"kind": "drive#permissionList", "permissions": [self.permission_json(p, shared_drive=shared) for p in perms]}
        return self._respond(request, out, "kind,nextPageToken,permissions(kind,id,type,role)")

    async def h_start_token(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        drive = request.query.get("driveId")
        if drive and (request.query.get("supportsAllDrives") != "true" or self._member_role(self._user_of(info), drive) is None):
            return self.error(404, f"Shared drive not found: {drive[:60]}", "notFound", location="driveId", locationType="parameter")
        return web.json_response({"kind": "drive#startPageToken", "startPageToken": str(self.next_change)})

    async def h_changes(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        user, q = self._user_of(info), request.query
        token = q.get("pageToken") or ""
        if not token:
            return self.error(400, "Required parameter: pageToken", "required", location="pageToken", locationType="parameter")
        if not token.isdigit() or int(token) > self.next_change:
            return self.error(400, "Invalid Value", "invalid", location="pageToken", locationType="parameter")
        if int(token) < self.oldest_change:
            return self.error(404, "Page token is no longer valid.", "notFound", location="pageToken", locationType="parameter")
        drive = q.get("driveId")
        all_drives = q.get("supportsAllDrives") == "true"
        include_all = q.get("includeItemsFromAllDrives") == "true"
        if drive and not (include_all and all_drives):
            return self.error(400, "The includeItemsFromAllDrives parameter must be set to true when driveId is set.", "badRequest")
        if drive and self._member_role(user, drive) is None:
            return self.error(404, f"Shared drive not found: {drive[:60]}", "notFound", location="driveId", locationType="parameter")
        include_removed = q.get("includeRemoved", "true") != "false"
        try:
            size = max(1, min(1000, int(q.get("pageSize") or 100)))
        except ValueError:
            return self.error(400, "Invalid value for pageSize", "invalid")
        visible = [c for c in self.changes if c["id"] >= int(token) and user in c["readers"]
                   and ((drive and c["drive"] == drive) or (not drive and (c["drive"] is None or (include_all and all_drives))))]
        page, rest = visible[:size], visible[size:]
        latest: dict[str, dict[str, Any]] = {}
        for c in page:                                     # one entry per file per page: its current state
            latest.pop(c["file"], None)
            latest[c["file"]] = c
        out_changes = []
        for c in latest.values():
            f = self.files.get(c["file"]) or {}
            removed = bool(f.get("deleted")) or not f or not self.can_read(user, f)
            if removed and not include_removed:
                continue
            ch: dict[str, Any] = {"kind": "drive#change", "changeType": "file", "time": c["time"], "removed": removed, "fileId": c["file"]}
            if c["drive"]:
                ch["driveId"] = c["drive"]
            if not removed:
                ch["file"] = self.file_json(f, user)
            out_changes.append(ch)
        out: dict[str, Any] = {"kind": "drive#changeList", "changes": out_changes}
        if rest:
            out["nextPageToken"] = str(rest[0]["id"])
        else:
            out["newStartPageToken"] = str(self.next_change)
        return self._respond(request, out, "kind,nextPageToken,newStartPageToken,changes(kind,changeType,time,removed,fileId,file(kind,id,name,mimeType))")

    async def h_watch(self, request: web.Request) -> web.Response:
        info, err = self.authenticate(request)
        if err:
            return err
        q = request.query
        if not (q.get("pageToken") or "").isdigit():
            return self.error(400, "Required parameter: pageToken", "required", location="pageToken", locationType="parameter")
        body = await request.json()
        cid, address = str(body.get("id") or ""), str(body.get("address") or "")
        if body.get("type") not in ("web_hook", "webhook") or not cid or not (address.startswith("https://") or address.startswith("http://127.0.0.1")):
            return self.error(400, "Invalid Value", "invalid", location="address" if cid else "id", locationType="parameter")
        if cid in self.channels:
            return self.error(400, f"Channel id {cid[:40]} not unique", "channelIdNotUnique")
        resource = "mockResource" + secrets.token_hex(8)
        exp = int(body.get("expiration") or int((self.clock() + 3600) * 1000))
        self.channels[cid] = {"id": cid, "resourceId": resource, "token": body.get("token"), "address": address, "expiration": exp,
                              "user": self._user_of(info), "drive": q.get("driveId"), "number": 0}
        return web.json_response({"kind": "api#channel", "id": cid, "resourceId": resource,
                                  "resourceUri": f"{self.url}/drive/v3/changes?alt=json&pageToken={q.get('pageToken')}",
                                  "token": body.get("token"), "expiration": str(exp)})

    async def h_stop(self, request: web.Request) -> web.Response:
        _info, err = self.authenticate(request)
        if err:
            return err
        body = await request.json()
        ch = self.channels.get(str(body.get("id") or ""))
        if ch is None or ch["resourceId"] != body.get("resourceId"):
            return self.error(404, "Channel not found for project", "notFound")
        self.channels.pop(ch["id"])
        return web.Response(status=204)

    # ------------------------------------------------------------------ notifications
    def notification(self, channel_id: str, *, state: str = "change", token: str | None = None) -> dict[str, str]:
        """The headers of one push to a channel (the body is empty). ``token=`` overrides the channel's token."""
        ch = self.channels[channel_id]
        ch["number"] += 1
        exp = datetime.fromtimestamp(ch["expiration"] / 1000, tz=timezone.utc)
        headers = {"X-Goog-Channel-ID": ch["id"], "X-Goog-Channel-Expiration": format_datetime(exp, usegmt=True),
                   "X-Goog-Resource-ID": ch["resourceId"], "X-Goog-Resource-URI": f"{self.url}/drive/v3/changes?alt=json",
                   "X-Goog-Resource-State": state, "X-Goog-Message-Number": str(ch["number"]), "Content-Length": "0"}
        tok = token if token is not None else ch.get("token")
        if tok:
            headers["X-Goog-Channel-Token"] = str(tok)
        return headers

    def _notify(self, drive: str | None, readers: set[str]) -> list[dict[str, str]]:
        return [self.notification(cid) for cid, ch in list(self.channels.items())
                if ch["user"] in readers and (ch["drive"] is None or ch["drive"] == drive)]

    async def send_notification(self, url: str, headers: Mapping[str, str]) -> int:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data=b"", headers=dict(headers)) as resp:
                return resp.status

    # ------------------------------------------------------------------ mutations (each returns the channel notifications)
    def _now(self) -> str:
        return iso_z(self.clock())

    def _record(self, f: dict[str, Any], before: set[str], *, bump: bool = True) -> list[dict[str, str]]:
        if bump:
            f["version"] = int(f.get("version") or 1) + 1
        readers = before | self.readers(f)
        self.changes.append({"id": self.next_change, "file": f["id"], "drive": f.get("driveId"), "time": self._now(), "readers": readers})
        self.next_change += 1
        return self._notify(f.get("driveId"), readers)

    def _subtree(self, fid: str) -> list[dict[str, Any]]:
        out, queue = [], [fid]
        while queue:
            cur = queue.pop(0)
            for f in self.files.values():
                if cur in (f.get("parents") or []) and not f.get("is_root"):
                    out.append(f)
                    if f["mimeType"] == FOLDER:
                        queue.append(f["id"])
        return out

    def add_file(self, *, file_id: str | None = None, name: str, parent: str, text: str = "", mime_type: str = GDOC, owner: str | None = None,
                 permissions: Iterable[Mapping[str, Any]] = (), content_kind: str = "text", by: str | None = None) -> list[dict[str, str]]:
        self._seq += 1
        fid = file_id or f"1MockNewFile{self._seq:06d}AAAA"
        drive = parent if parent in self.drives else (self.files.get(parent) or {}).get("driveId")
        f = {"id": fid, "name": name, "mimeType": mime_type, "parents": [parent], "driveId": drive, "owner": None if drive else (owner or by),
             "createdTime": self._now(), "modifiedTime": self._now(), "version": 1, "text": text, "content_kind": content_kind,
             "permissions": [dict(p) for p in permissions], "lastModifyingUser": by or owner}
        self.files[fid] = f
        return self._record(f, set(), bump=False)

    def edit_file(self, file_id: str, text: str, *, by: str | None = None, name: str | None = None) -> list[dict[str, str]]:
        f = self.files[file_id]
        before = self.readers(f)
        f["text"] = text
        if name is not None:
            f["name"] = name
        f["modifiedTime"] = self._now()
        if by:
            f["lastModifyingUser"] = by
        return self._record(f, before)

    def set_permissions(self, file_id: str, permissions: Iterable[Mapping[str, Any]], *, inherited_disabled: bool | None = None) -> list[dict[str, str]]:
        """Replace the file's own permissions (``modifiedTime`` stays; ``version`` moves, as Drive's does)."""
        f = self.files[file_id]
        before = self.readers(f)
        f["permissions"] = [dict(p) for p in permissions]
        if inherited_disabled is not None:
            f["inheritedPermissionsDisabled"] = bool(inherited_disabled)
        return self._record(f, before)

    def move_file(self, file_id: str, new_parent: str) -> list[dict[str, str]]:
        f = self.files[file_id]
        before = self.readers(f)
        f["parents"] = [new_parent]
        drive = new_parent if new_parent in self.drives else (self.files.get(new_parent) or {}).get("driveId")
        if drive != f.get("driveId"):
            for x in [f, *self._subtree(file_id)]:
                x["driveId"] = drive
                if drive:
                    x["owner"] = None
        return self._record(f, before)

    def trash_file(self, file_id: str) -> list[dict[str, str]]:
        """Into the trash; a folder's contents go with it (each with its own change)."""
        out: list[dict[str, str]] = []
        f = self.files[file_id]
        for x in [f, *self._subtree(file_id)]:
            before = self.readers(x)
            x["trashed"] = True
            x["explicitly_trashed"] = x is f
            out += self._record(x, before)
        return out

    def delete_file(self, file_id: str) -> list[dict[str, str]]:
        """Deleted for good (emptied from the trash); a folder's contents go with it."""
        out: list[dict[str, str]] = []
        f = self.files[file_id]
        for x in [f, *self._subtree(file_id)]:
            before = self.readers(x)
            x["deleted"] = True
            out += self._record(x, before)
        return out

    def remove_drive_member(self, drive_id: str, email: str) -> None:
        d = self.drives[drive_id]
        d["members"] = [m for m in d.get("members") or [] if str(m["email"]).lower() != email.lower()]

    def expire_changes(self) -> None:
        """Every page token older than the current one now answers 404."""
        self.oldest_change = self.next_change


__all__ = ["DriveMock", "docx_bytes", "pdf_bytes", "start_drive_mock"]

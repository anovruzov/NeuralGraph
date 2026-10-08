"""A faithful offline Gmail API v1 and Cloud Pub/Sub push for tests and demos.

Start it with :func:`start_gmail_mock`::

    url, gm = await start_gmail_mock(load_fixture("gmail_acme"), clock=FakeClock(...))
    # connector config: {"api_base": url}; OAuth: OAuthAppConfig(client_id, Secret(secret), oauth_base=url, api_base=url,
    # allow_loopback_http=True); a push: d = gm.deliver(...) -> PushDelivery(headers, body, query)
    await gm.close()

Modelled behaviour (Gmail API reference as understood when this was written; re-verify against developers.google.com):

* ``/gmail/v1/users/{me|address}/``: ``profile`` (``emailAddress``, ``messagesTotal``, ``threadsTotal``, ``historyId``),
  ``labels`` (system and user labels), ``messages`` (``labelIds`` repeated and ANDed, ``q`` with ``after:``/``before:`` as
  epoch seconds or ``YYYY/MM/DD``, strict comparisons on ``internalDate``; other search terms are ignored by this mock;
  ``includeSpamTrash`` default false; ``maxResults`` default 100, at most 500; newest first; an opaque ``pageToken``; no
  ``messages`` key at all when nothing matches), ``messages/{id}`` (``format=full|metadata|minimal|raw``; ``full`` has the
  MIME tree with base64url bodies and attachments as ``attachmentId`` references that change on every fetch, as Gmail's do),
  ``history`` (``startHistoryId`` required, records strictly after it in order, ``labelId`` matches the label before or
  after the change, ``historyTypes`` repeated, ``maxResults`` default 100, at most 500, ``historyId`` = the mailbox's
  current id; a start id older than the kept history answers **404** ``notFound``), ``watch`` and ``stop``.
* Scopes: ``gmail.readonly`` (or ``gmail.modify`` / ``https://mail.google.com/``) reads everything; ``gmail.metadata`` cannot
  fetch ``format=full`` or ``raw`` (403).
* Faults (from :class:`~mycelic.ingest.mocks.google_common.GoogleMockBase` and the base mock): ``rate_limit_next`` (403
  ``userRateLimitExceeded`` / ``rateLimitExceeded``, 429), ``revoke_token``, ``expire_token``, ``fail_next``,
  ``crash_after_pages``, :meth:`expire_history`; the request log never holds tokens.
* Mutations (:meth:`deliver`, :meth:`delete_message`, :meth:`modify_labels`, :meth:`trash`) append history records and
  return the Pub/Sub push Gmail would cause: a :class:`PushDelivery` (headers, raw JSON body, and the push URL's query
  with ``token``, the shared secret). ``token=`` makes a wrong one.
* OAuth: Google's ``/o/oauth2/auth`` and ``/token`` with PKCE, and ``/revoke``.
"""
from __future__ import annotations

import base64
import copy
import html as _html
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any, Callable, Iterable, Mapping

import aiohttp
from aiohttp import web

from .common import iso_z, load_fixture, parse_iso
from .google_common import GoogleMockBase

READONLY = "https://www.googleapis.com/auth/gmail.readonly"
FULL_SCOPES = frozenset({READONLY, "https://mail.google.com/", "https://www.googleapis.com/auth/gmail.modify"})
METADATA = "https://www.googleapis.com/auth/gmail.metadata"
SYSTEM_LABEL_NAMES = {"INBOX": "INBOX", "SENT": "SENT", "DRAFT": "DRAFT", "SPAM": "SPAM", "TRASH": "TRASH", "UNREAD": "UNREAD",
                      "STARRED": "STARRED", "IMPORTANT": "IMPORTANT", "CHAT": "CHAT", "CATEGORY_PERSONAL": "CATEGORY_PERSONAL",
                      "CATEGORY_SOCIAL": "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS": "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES": "CATEGORY_UPDATES",
                      "CATEGORY_FORUMS": "CATEGORY_FORUMS"}
_DATE_TERM = re.compile(r"^(after|before|older|newer):(\d{4}/\d{1,2}/\d{1,2}|\d{1,12})$", re.IGNORECASE)


@dataclass
class PushDelivery:
    """A Cloud Pub/Sub push as it reaches the endpoint: headers, the raw JSON body, and the push URL's query string."""
    headers: dict[str, str]
    body: bytes
    query: dict[str, str] = field(default_factory=dict)


async def start_gmail_mock(fixture: Mapping[str, Any] | None = None, *, clock: Callable[[], float] | None = None, latency: float = 0.0,
                           host: str = "127.0.0.1", port: int = 0) -> tuple[str, "GmailMock"]:
    """Start a mock on loopback; returns ``(base_url, controller)``. ``fixture`` defaults to ``gmail_acme``."""
    mock = GmailMock(fixture if fixture is not None else load_fixture("gmail_acme"), clock=clock, latency=latency)
    return await mock.start(host, port), mock


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


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


class GmailMock(GoogleMockBase):
    name = "gmail"
    read_scopes = FULL_SCOPES | {METADATA}

    def __init__(self, fixture: Mapping[str, Any], *, clock: Callable[[], float] | None = None, latency: float = 0.0) -> None:
        fx = copy.deepcopy(dict(fixture))
        self.mailbox = str(fx["mailbox"]).lower()
        self.labels: dict[str, dict[str, Any]] = {lb["id"]: dict(lb) for lb in fx.get("labels") or []}
        self.history_id = int(fx.get("history_start") or 700000)
        self.oldest_history = self.history_id                # start ids below this answer 404
        self.history: list[dict[str, Any]] = []
        self.messages: dict[str, dict[str, Any]] = {}
        self.watch: dict[str, Any] | None = None
        self.history_types_seen: list[list[str]] = []      # repeated historyTypes per history request (the request log keeps one value)
        self._seq = 0
        self._push_seq = 0
        initial = sorted(fx.get("messages") or [], key=lambda m: m["internalDate"])
        for i, m in enumerate(initial):                     # the fixture's messages predate the kept history
            self.messages[m["id"]] = self._normal(m, history_id=self.history_id - len(initial) + i)
        super().__init__(fx, clock=clock, latency=latency)

    # ------------------------------------------------------------------ routes
    def _api_routes(self, app: web.Application) -> None:
        p = "/gmail/v1/users/{user}"
        r = app.router
        r.add_get(p + "/profile", self.h_profile)
        r.add_get(p + "/labels", self.h_labels)
        r.add_get(p + "/labels/{label_id}", self.h_label)
        r.add_get(p + "/messages", self.h_list)
        r.add_get(p + "/messages/{message_id}", self.h_get)
        r.add_get(p + "/history", self.h_history)
        r.add_post(p + "/watch", self.h_watch)
        r.add_post(p + "/stop", self.h_stop)

    # ------------------------------------------------------------------ model
    def _normal(self, m: Mapping[str, Any], *, history_id: int) -> dict[str, Any]:
        d = dict(m)
        d["labelIds"] = list(m.get("labelIds") or [])
        d["internal_ms"] = int(parse_iso(m["internalDate"]) * 1000)
        d["historyId"] = int(history_id)
        d.setdefault("threadId", d["id"])
        d.setdefault("to", [])
        d.setdefault("cc", [])
        d.setdefault("attachments", [])
        d.setdefault("headers", {})
        return d

    def _live(self) -> list[dict[str, Any]]:
        return [m for m in self.messages.values() if not m.get("deleted")]

    def _user_ok(self, request: web.Request, info: Mapping[str, Any]) -> web.Response | None:
        user = request.match_info.get("user", "")
        if user not in ("me", self.mailbox) or str(info.get("user") or "").lower() != self.mailbox:
            return self.error(403, f"Delegation denied for {self.mailbox}", "forbidden")
        return None

    async def _guard(self, request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
        info, err = self.authenticate(request)
        if err is not None:
            return None, err
        bad = self._user_ok(request, info)       # type: ignore[arg-type]
        return (None, bad) if bad is not None else (info, None)

    def _headers(self, m: Mapping[str, Any]) -> list[dict[str, str]]:
        dt = datetime.fromtimestamp(m["internal_ms"] / 1000, tz=timezone.utc)
        h = [("Delivered-To", self.mailbox),
             ("Received", f"by 2002:a05:6a10:mock with SMTP id mock{m['id'][-6:]}; {format_datetime(dt)}"),
             ("MIME-Version", "1.0"), ("Date", format_datetime(dt)), ("Message-ID", m.get("messageId") or f"<{m['id']}@mail.gmail.com>"),
             ("Subject", m.get("subject") or ""), ("From", m["from"])]
        if m.get("to"):
            h.append(("To", ", ".join(m["to"])))
        if m.get("cc"):
            h.append(("Cc", ", ".join(m["cc"])))
        if m.get("inReplyTo"):
            h.append(("In-Reply-To", m["inReplyTo"]))
        if m.get("references"):
            h.append(("References", " ".join(m["references"])))
        h += [(k, str(v)) for k, v in (m.get("headers") or {}).items()]
        return [{"name": k, "value": v} for k, v in h]

    @staticmethod
    def _leaf(mime: str, text: str, part_id: str) -> dict[str, Any]:
        data = text.encode("utf-8")
        return {"partId": part_id, "mimeType": mime, "filename": "",
                "headers": [{"name": "Content-Type", "value": f'{mime}; charset="UTF-8"'}, {"name": "Content-Transfer-Encoding", "value": "quoted-printable"}],
                "body": {"size": len(data), "data": _b64url(data)}}

    def _mime(self, m: Mapping[str, Any]) -> dict[str, Any]:
        text, markup, atts = m.get("text"), m.get("html"), m.get("attachments") or []
        prefix = "0." if atts else ""
        if text and markup:
            body: dict[str, Any] = {"partId": "0" if atts else "", "mimeType": "multipart/alternative", "filename": "",
                                    "headers": [{"name": "Content-Type", "value": 'multipart/alternative; boundary="000000000000a1b2c3"'}],
                                    "body": {"size": 0}, "parts": [self._leaf("text/plain", text, prefix + "0"), self._leaf("text/html", markup, prefix + "1")]}
        elif markup:
            body = self._leaf("text/html", markup, "0" if atts else "")
        else:
            body = self._leaf("text/plain", text or "", "0" if atts else "")
        if not atts:
            top = body
        else:
            parts = [body]
            for i, a in enumerate(atts, start=1):
                parts.append({"partId": str(i), "mimeType": a["mimeType"], "filename": a["filename"],
                              "headers": [{"name": "Content-Type", "value": f'{a["mimeType"]}; name="{a["filename"]}"'},
                                          {"name": "Content-Disposition", "value": f'attachment; filename="{a["filename"]}"'},
                                          {"name": "Content-Transfer-Encoding", "value": "base64"},
                                          {"name": "X-Attachment-Id", "value": f"f_mock{i}"}],
                              # Gmail's attachmentId is not stable: a new one on every fetch
                              "body": {"attachmentId": "ANGjdJ" + secrets.token_urlsafe(40), "size": int(a.get("size") or 0)}})
            top = {"partId": "", "mimeType": "multipart/mixed", "filename": "", "headers": [], "body": {"size": 0}, "parts": parts}
        top = dict(top)
        ct = next((h["value"] for h in top.get("headers") or [] if h["name"] == "Content-Type"), 'multipart/mixed; boundary="000000000000d4e5f6"')
        top["headers"] = self._headers(m) + [{"name": "Content-Type", "value": ct}]
        top["partId"] = ""
        return top

    def _snippet(self, m: Mapping[str, Any]) -> str:
        text = m.get("text") or re.sub(r"<[^>]+>", " ", m.get("html") or "")
        return _html.escape(" ".join(text.split())[:140], quote=True)

    def message_json(self, m: Mapping[str, Any], fmt: str = "full", metadata_headers: Iterable[str] = ()) -> dict[str, Any]:
        size = len((m.get("text") or "").encode()) + len((m.get("html") or "").encode()) + sum(int(a.get("size") or 0) for a in m.get("attachments") or [])
        d: dict[str, Any] = {"id": m["id"], "threadId": m["threadId"], "labelIds": list(m["labelIds"]), "snippet": self._snippet(m),
                             "historyId": str(m["historyId"]), "internalDate": str(m["internal_ms"]), "sizeEstimate": size + 1200}
        if fmt == "minimal":
            return d
        if fmt == "metadata":
            wanted = {x.lower() for x in metadata_headers}
            hdrs = [h for h in self._headers(m) if not wanted or h["name"].lower() in wanted]
            d["payload"] = {"partId": "", "mimeType": "multipart/mixed" if m.get("attachments") else "text/plain", "filename": "", "headers": hdrs,
                            "body": {"size": 0}}
            return d
        if fmt == "raw":
            d["raw"] = _b64url(self._rfc822(m))
            return d
        d["payload"] = self._mime(m)
        return d

    def _rfc822(self, m: Mapping[str, Any]) -> bytes:
        msg = EmailMessage()
        for h in self._headers(m):
            if h["name"] not in ("MIME-Version",):
                msg[h["name"]] = h["value"]
        msg.set_content(m.get("text") or "")
        if m.get("html"):
            msg.add_alternative(m["html"], subtype="html")
        for a in m.get("attachments") or []:
            main, _, sub = str(a["mimeType"]).partition("/")
            msg.add_attachment(b"\0" * int(a.get("size") or 0), maintype=main, subtype=sub or "octet-stream", filename=a["filename"])
        return msg.as_bytes()

    def label_json(self, lb: Mapping[str, Any]) -> dict[str, Any]:
        d = {"id": lb["id"], "name": lb.get("name") or SYSTEM_LABEL_NAMES.get(lb["id"], lb["id"]), "type": lb.get("type") or "user"}
        if d["type"] == "user":
            d.update({"messageListVisibility": "show", "labelListVisibility": "labelShow"})
        elif lb["id"] in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "UNREAD"):
            d.update({"messageListVisibility": "hide", "labelListVisibility": "labelShow"})
        return d

    # ------------------------------------------------------------------ handlers
    async def h_profile(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        live = self._live()
        return web.json_response({"emailAddress": self.mailbox, "messagesTotal": len(live), "threadsTotal": len({m["threadId"] for m in live}),
                                  "historyId": str(self.history_id)})

    async def h_labels(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        return web.json_response({"labels": [self.label_json(lb) for lb in self.labels.values()]})

    async def h_label(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        lb = self.labels.get(request.match_info["label_id"])
        if lb is None:
            return self.error(404, "Requested entity was not found.", "notFound")
        d = self.label_json(lb)
        inside = [m for m in self._live() if lb["id"] in m["labelIds"]]
        d.update({"messagesTotal": len(inside), "messagesUnread": sum(1 for m in inside if "UNREAD" in m["labelIds"]),
                  "threadsTotal": len({m["threadId"] for m in inside}), "threadsUnread": len({m["threadId"] for m in inside if "UNREAD" in m["labelIds"]})})
        return web.json_response(d)

    @staticmethod
    def _date_bound(value: str) -> float:
        if value.isdigit():
            return float(value)
        y, mo, d = (int(x) for x in value.split("/"))
        return datetime(y, mo, d, tzinfo=timezone.utc).timestamp()

    async def h_list(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        q = request.query
        label_ids = q.getall("labelIds", [])
        if any(x not in self.labels for x in label_ids):
            return self.error(400, "Invalid label: " + ",".join(x for x in label_ids if x not in self.labels)[:60], "invalidArgument")
        after = before = None
        for term in (q.get("q") or "").split():
            m = _DATE_TERM.match(term)
            if m:
                bound = self._date_bound(m.group(2))
                if m.group(1).lower() in ("after", "newer"):
                    after = bound
                else:
                    before = bound
        include_st = q.get("includeSpamTrash") == "true" or any(x in ("SPAM", "TRASH") for x in label_ids)
        try:
            max_results = max(1, min(500, int(q.get("maxResults") or 100)))
        except ValueError:
            return self.error(400, "Invalid value for maxResults", "invalid")
        msgs = []
        for m in self._live():
            if any(x not in m["labelIds"] for x in label_ids):
                continue
            if not include_st and {"SPAM", "TRASH"} & set(m["labelIds"]):
                continue
            secs = m["internal_ms"] / 1000
            if (after is not None and not secs > after) or (before is not None and not secs < before):
                continue
            msgs.append(m)
        msgs.sort(key=lambda m: (m["internal_ms"], m["id"]), reverse=True)
        off = _offset(q.get("pageToken"), "list")
        if off is None:
            return self.error(400, "Invalid pageToken", "invalid", location="pageToken", locationType="parameter")
        chunk = msgs[off: off + max_results]
        out: dict[str, Any] = {"resultSizeEstimate": len(msgs)}
        if chunk:
            out["messages"] = [{"id": m["id"], "threadId": m["threadId"]} for m in chunk]
        if off + max_results < len(msgs):
            out["nextPageToken"] = _token(off + max_results, "list")
        return web.json_response(out)

    async def h_get(self, request: web.Request) -> web.Response:
        info, err = await self._guard(request)
        if err:
            return err
        m = self.messages.get(request.match_info["message_id"])
        if m is None or m.get("deleted"):
            return self.error(404, "Requested entity was not found.", "notFound")
        fmt = request.query.get("format", "full")
        if fmt not in ("full", "metadata", "minimal", "raw"):
            return self.error(400, f"Invalid value at 'format' ({fmt[:20]})", "invalidArgument")
        if fmt in ("full", "raw") and not set(info.get("scopes") or []) & FULL_SCOPES:       # type: ignore[union-attr]
            return self.error(403, "Metadata scope doesn't allow format FULL", "forbidden")
        return web.json_response(self.message_json(m, fmt, request.query.getall("metadataHeaders", [])))

    def _history_json(self, h: Mapping[str, Any]) -> dict[str, Any]:
        ref = {"id": h["message"], "threadId": h["thread"]}
        msg = {**ref, "labelIds": list(h["labels_after"])}
        rec: dict[str, Any] = {"id": str(h["id"]), "messages": [ref]}
        if h["type"] == "messageAdded":
            rec["messagesAdded"] = [{"message": msg}]
        elif h["type"] == "messageDeleted":
            rec["messagesDeleted"] = [{"message": ref}]
        elif h["type"] == "labelAdded":
            rec["labelsAdded"] = [{"message": msg, "labelIds": list(h["changed"])}]
        else:
            rec["labelsRemoved"] = [{"message": msg, "labelIds": list(h["changed"])}]
        return rec

    async def h_history(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        q = request.query
        start = q.get("startHistoryId") or ""
        if not start.isdigit():
            return self.error(400, "Invalid startHistoryId", "invalidArgument")
        if int(start) < self.oldest_history:
            return self.error(404, "Requested entity was not found.", "notFound")
        label = q.get("labelId")
        types = q.getall("historyTypes", [])
        self.history_types_seen.append(list(types))
        if any(t not in ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved") for t in types):
            return self.error(400, "Invalid value for historyTypes", "invalidArgument")
        try:
            max_results = max(1, min(500, int(q.get("maxResults") or 100)))
        except ValueError:
            return self.error(400, "Invalid value for maxResults", "invalid")
        recs = [h for h in self.history if h["id"] > int(start) and (not types or h["type"] in types)
                and (not label or label in h["labels_before"] or label in h["labels_after"])]
        off = _offset(q.get("pageToken"), f"hist{start}")
        if off is None:
            return self.error(400, "Invalid pageToken", "invalid", location="pageToken", locationType="parameter")
        chunk = recs[off: off + max_results]
        out: dict[str, Any] = {"historyId": str(self.history_id)}
        if chunk:
            out["history"] = [self._history_json(h) for h in chunk]
        if off + max_results < len(recs):
            out["nextPageToken"] = _token(off + max_results, f"hist{start}")
        return web.json_response(out)

    async def h_watch(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        body = await request.json()
        topic = str((body or {}).get("topicName") or "")
        if not re.match(r"^projects/[^/]+/topics/[^/]+$", topic):
            return self.error(400, "Invalid topicName does not match projects/*/topics/*", "invalidArgument")
        self.watch = {"topicName": topic, "labelIds": list(body.get("labelIds") or []), "labelFilterBehavior": body.get("labelFilterBehavior") or "include"}
        return web.json_response({"historyId": str(self.history_id), "expiration": str(int((self.clock() + 7 * 86400) * 1000))})

    async def h_stop(self, request: web.Request) -> web.Response:
        _info, err = await self._guard(request)
        if err:
            return err
        self.watch = None
        return web.Response(status=204)

    # ------------------------------------------------------------------ mutations (each returns the push Gmail would cause)
    def _bump(self) -> int:
        self.history_id += 1
        return self.history_id

    def _new_id(self) -> str:
        self._seq += 1
        return f"{0x19a0000000000000 + self._seq:x}"

    def _record(self, kind: str, m: dict[str, Any], *, before: list[str], changed: list[str]) -> int:
        hid = self._bump()
        m["historyId"] = hid
        self.history.append({"id": hid, "type": kind, "message": m["id"], "thread": m["threadId"], "labels_before": list(before),
                             "labels_after": [] if kind == "messageDeleted" else list(m["labelIds"]), "changed": list(changed)})
        return hid

    def deliver(self, *, sender: str, to: Iterable[str], subject: str, text: str | None = None, html: str | None = None,
                labels: Iterable[str] = ("INBOX", "UNREAD"), cc: Iterable[str] = (), thread_id: str | None = None, message_id: str | None = None,
                in_reply_to: str | None = None, headers: Mapping[str, str] | None = None, attachments: Iterable[Mapping[str, Any]] = (),
                at: str | None = None) -> PushDelivery:
        """A new message in the mailbox (received, or sent with ``labels=("SENT",)``)."""
        mid = self._new_id()
        raw = {"id": mid, "threadId": thread_id or mid, "labelIds": list(labels), "internalDate": at or iso_z(self.clock()), "from": sender,
               "to": list(to), "cc": list(cc), "subject": subject, "messageId": message_id or f"<mock{mid}@mail.gmail.com>", "inReplyTo": in_reply_to,
               "references": [in_reply_to] if in_reply_to else [], "headers": dict(headers or {}), "text": text, "html": html,
               "attachments": [dict(a) for a in attachments]}
        m = self._normal(raw, history_id=0)
        self.messages[mid] = m
        self._record("messageAdded", m, before=[], changed=list(m["labelIds"]))
        return self.push_delivery()

    def delete_message(self, message_id: str) -> PushDelivery:
        """Permanently deleted (gone from the trash, or deleted by the API)."""
        m = self.messages[message_id]
        m["deleted"] = True
        self._record("messageDeleted", m, before=list(m["labelIds"]), changed=[])
        return self.push_delivery()

    def modify_labels(self, message_id: str, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> PushDelivery:
        m = self.messages[message_id]
        for lab in add:
            if lab not in m["labelIds"]:
                before = list(m["labelIds"])
                m["labelIds"].append(lab)
                self._record("labelAdded", m, before=before, changed=[lab])
        for lab in remove:
            if lab in m["labelIds"]:
                before = list(m["labelIds"])
                m["labelIds"].remove(lab)
                self._record("labelRemoved", m, before=before, changed=[lab])
        return self.push_delivery()

    def trash(self, message_id: str) -> PushDelivery:
        """Moved to the trash: ``TRASH`` added, ``INBOX`` removed; user labels stay, as Gmail keeps them."""
        return self.modify_labels(message_id, add=["TRASH"], remove=["INBOX"])

    def expire_history(self) -> None:
        """Gmail keeps history for a limited time: every start id older than the current one now answers 404."""
        self.oldest_history = self.history_id

    # ------------------------------------------------------------------ Pub/Sub push
    def push_delivery(self, *, history_id: int | None = None, token: str | None = None, message_id: str | None = None) -> PushDelivery:
        """The POST a Pub/Sub push subscription makes for this mailbox: ``message.data`` is base64 of ``{"emailAddress",
        "historyId"}``; the push URL carries ``?token=`` (default: the fixture's dummy ``app.push_token``)."""
        self._push_seq += 1
        mid = message_id or str(2070443601311540 + self._push_seq)
        published = datetime.fromtimestamp(self.clock(), tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        data = base64.b64encode(json.dumps({"emailAddress": self.mailbox, "historyId": int(history_id or self.history_id)}).encode()).decode()
        body = {"message": {"data": data, "messageId": mid, "message_id": mid, "publishTime": published, "publish_time": published, "attributes": {}},
                "subscription": str(self.app_config.get("pubsub_subscription") or "projects/mock/subscriptions/gmail-push")}
        headers = {"Content-Type": "application/json", "User-Agent": "APIs-Google; (+https://developers.google.com/webmasters/APIs-Google.html)",
                   "From": "noreply@google.com"}
        return PushDelivery(headers=headers, body=json.dumps(body).encode("utf-8"),
                            query={"token": token if token is not None else str(self.app_config.get("push_token") or "")})

    async def send_push(self, url: str, delivery: PushDelivery) -> int:
        """POST a push to ``url`` (a Mycelic webhook endpoint) with its query token; returns the HTTP status."""
        async with aiohttp.ClientSession() as s:
            async with s.post(url, params=delivery.query, data=delivery.body, headers=delivery.headers) as resp:
                return resp.status


__all__ = ["GmailMock", "PushDelivery", "start_gmail_mock"]

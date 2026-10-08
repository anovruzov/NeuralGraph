"""Canonical ingestion event v1 (docs/mycelic/INGESTION.md §4).

Every connector turns provider objects into :class:`CanonicalEvent` values; nothing downstream knows which app produced
them. The module also owns the identity and dedupe keys, the hashes, the order key and the ``source_root_id`` rules:

* ``record_key = "rk1_" + H("rk1", tenant, holder, source_app, source_account_id, object_type, object_id)[:40]`` — the
  record's stable identity inside one holder. The connection row (``connector_id``) is *not* part of it, so reconnecting
  the same account never duplicates objects.
* ``object_key`` — the same object's identity *across* holders (no holder in it): an object reached through an org
  connector (unit holder) and a personal connector (user holder) keeps one canonical identity and one root, while each
  holder keeps its own row.
* ``event_key`` — dedupe of deliveries: same record, kind, version, content and deletion status collapse.
* ``order_key = "<UTC µs timestamp>|<tiebreak>"`` — per-object total order (the provider is the only writer).
* ``content_hash`` over the canonical body; the root fingerprint is deliberately lossier (``mycelic.util.fingerprint``).

Content is untrusted data: nothing here interprets it beyond splitting quotes, forwards and signatures.
"""
from __future__ import annotations

import json
import re
import unicodedata
import zlib
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ..util import canonical_json, fingerprint, parse_iso, sha256
from .normalize import MIN_OWN_CHARS, canonical_body, non_space_len, split_body

SCHEMA_VERSION = 1

EVENT_KINDS = ("message", "message_version", "document", "document_chunk", "conversation", "event", "deletion", "redaction")
CONTENT_KINDS = ("message", "document", "event")
DELETION_STATUSES = ("live", "deleted_at_source", "redacted", "access_lost", "purged")
SENSITIVITIES = ("public", "internal", "confidential", "restricted")
VISIBILITIES = ("public", "members", "private")
_EPOCH_RE = re.compile(r"\d{9,11}(?:\.\d{1,9})?")


def H(*parts: str) -> str:
    """SHA-256 over the parts joined by the unit separator (the ``mycelic.util.sha256`` convention)."""
    return sha256(*[str(p) for p in parts])


# ---------------------------------------------------------------------------------------------- value types
@dataclass(frozen=True)
class Permissions:
    """Source ACL carried by every record: ``visibility`` plus ``member_ids`` and/or ``membership_ref``.

    ``public``: anyone the holder's export policy answers. ``members``: only an audience entirely inside the members.
    ``private``: same rule, and not exportable until the owner opts the source in. ``member_ids`` are principal ids
    (Mycelic user ids after the connector's principal mapping); ``membership_ref`` names a membership list kept in the
    holder (``acl_memberships``) and is resolved at use time, so a member leaving narrows access for every record at once.
    """
    visibility: str = "private"
    member_ids: tuple[str, ...] = ()
    membership_ref: str | None = None
    acl_version: str | None = None

    def __post_init__(self) -> None:
        if self.visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {VISIBILITIES}")
        object.__setattr__(self, "member_ids", tuple(sorted({str(m) for m in self.member_ids if m})))

    def to_dict(self) -> dict[str, Any]:
        return {"visibility": self.visibility, "member_ids": list(self.member_ids), "membership_ref": self.membership_ref,
                "acl_version": self.acl_version}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "Permissions":
        d = d or {}
        return cls(visibility=str(d.get("visibility") or "private"), member_ids=tuple(d.get("member_ids") or ()),
                   membership_ref=d.get("membership_ref") or None, acl_version=d.get("acl_version") or None)


@dataclass(frozen=True)
class RetentionPolicy:
    policy_id: str = "default"
    retain_days: int | None = None
    keep_versions: str = "latest"           # latest | all
    delete_on_source_delete: bool = True    # False only under legal hold (admin-set, audited)


@dataclass(frozen=True)
class AttachmentRef:
    attachment_id: str
    filename: str = ""
    content_type: str = "application/octet-stream"
    size_bytes: int | None = None
    sha256: str | None = None
    provider_url: str | None = None
    text_extractable: bool = False


@dataclass(frozen=True)
class DerivedFrom:
    relation: str                           # forward | share | crosspost | quote | copy | moved_from | bot_relay
    source_app: str | None = None
    source_account_id: str | None = None
    source_object_type: str | None = None
    source_object_id: str | None = None
    url: str | None = None
    rfc822_message_id: str | None = None


# ---------------------------------------------------------------------------------------------- keys and hashes
def record_key(tenant_id: str, holder_id: str, source_app: str, source_account_id: str, source_object_type: str,
               source_object_id: str) -> str:
    return "rk1_" + H("rk1", tenant_id, holder_id, source_app, source_account_id, source_object_type, source_object_id)[:40]


def record_id_for(rkey: str) -> str:
    """``documents.doc_id`` / ``chats.chat_id`` of the record."""
    return "rec_" + rkey[4:28]


def object_key(tenant_id: str, source_app: str, source_account_id: str, source_object_type: str, source_object_id: str) -> str:
    return "ok1_" + H("ok1", tenant_id, source_app, source_account_id, source_object_type, source_object_id)[:40]


def version_key(rkey: str, source_version: str, chash: str) -> str:
    return rkey + "@" + H(source_version or chash)[:16]


def event_key(rkey: str, kind: str, source_version: str, chash: str, deletion_status: str, order_key: str = "") -> str:
    """Deletions and redactions carry no version and an empty body, so their position (``order_key``) tells a second
    redaction (after the content was shown again) from a re-delivery of the first."""
    if kind in ("deletion", "redaction") and order_key:
        return "ek1_" + H("ek1", rkey, kind, source_version or "", chash, deletion_status, order_key)[:40]
    return "ek1_" + H("ek1", rkey, kind, source_version or "", chash, deletion_status)[:40]


def normalize_ts(value: Any) -> str | None:
    """ISO-8601 UTC with microseconds (``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``). Accepts ISO strings, dates and epoch numbers
    (seconds, or provider strings like ``"1712345678.000200"``). ``None`` when absent or unparsable."""
    if value is None or value == "":
        return None
    dt: datetime | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromtimestamp(float(s), tz=timezone.utc) if _EPOCH_RE.fullmatch(s) else None
        except (OverflowError, ValueError, OSError):
            dt = None
        if dt is None:
            dt = parse_iso(s if len(s) > 10 else f"{s}T00:00:00+00:00")
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def make_order_key(*, updated_at: str | None, created_at: str | None, observed_at: str | None, tiebreak: str) -> str:
    ts = normalize_ts(updated_at) or normalize_ts(created_at) or normalize_ts(observed_at) or "0000-00-00T00:00:00.000000+00:00"
    return f"{ts}|{tiebreak}"


def content_hash(title: str, body: str, content_type: str, attachments: Iterable[AttachmentRef] = ()) -> str:
    att = sorted((a.sha256 or a.attachment_id) for a in attachments)
    return "ch1_" + H("ch1", unicodedata.normalize("NFC", title or "").strip(), canonical_body(body), content_type or "", *att)


def metadata_hash(*, labels: Iterable[str] = (), state: str | None = None, participants: Iterable[str] = (),
                  permissions: Permissions | None = None, container_name: str | None = None) -> str:
    return "mh1_" + H("mh1", canonical_json({"labels": sorted(set(labels)), "state": state or "", "participants": sorted(set(participants)),
                                             "permissions": (permissions or Permissions()).to_dict(), "container": container_name or ""}))


# ---------------------------------------------------------------------------------------------- roots (§4.5)
@dataclass(frozen=True)
class RootResult:
    root: str | None
    root_known: bool
    method: str              # content | title | explicit | pure_copy | unknown
    own_text: str            # the body that is indexed for this record (quotes and forwards removed)
    forwarded: tuple[str, ...] = ()
    quoted: tuple[str, ...] = ()


def compute_root(title: str, body: str, content_type: str = "text/plain", *, derived_from: Iterable[DerivedFrom] = (),
                 hints: Mapping[str, Any] | None = None,
                 resolve_explicit: Callable[[DerivedFrom], str | None] | None = None) -> RootResult:
    """Rules of §4.5, in order: explicit linkage to a known original, pure copies of a forwarded segment, the author's own
    text, the title; unknown independence for empty records and unresolvable bot relays."""
    hints = hints or {}
    split = split_body(body, content_type)
    fwd = tuple(s.text for s in split.forwarded)
    quoted = tuple(s.text for s in split.quoted)
    own = split.own_text
    if resolve_explicit is not None:
        for d in derived_from:
            if d.relation in ("forward", "share", "crosspost", "copy", "moved_from", "bot_relay"):
                root = resolve_explicit(d)
                if root:
                    return RootResult(root, True, "explicit", own or (fwd[0] if fwd else ""), fwd, quoted)
    if hints.get("is_bot") and any(d.relation == "bot_relay" for d in derived_from):
        return RootResult(None, False, "unknown", own, fwd, quoted)
    if fwd and non_space_len(own) < MIN_OWN_CHARS:
        # a pure copy: the record's content *is* the forwarded text, so it shares that text's root
        return RootResult(fingerprint(fwd[0]), True, "pure_copy", fwd[0], fwd, quoted)
    if own.strip():
        return RootResult(fingerprint(own), True, "content", own, fwd, quoted)
    if (title or "").strip():
        return RootResult(fingerprint(title), True, "title", "", fwd, quoted)
    return RootResult(None, False, "unknown", "", fwd, quoted)


# ---------------------------------------------------------------------------------------------- the event
@dataclass
class CanonicalEvent:
    schema_version: int
    kind: str
    # identity
    tenant_id: str
    holder_id: str
    connector_id: str
    source_app: str
    source_account_id: str
    source_object_type: str
    source_object_id: str
    source_version: str
    source_event_id: str | None
    # structure
    conversation_id: str | None
    thread_id: str | None
    parent_message_id: str | None
    author_id: str | None
    participant_ids: tuple[str, ...]
    # time (ISO-8601 UTC, microseconds)
    created_at: str | None
    updated_at: str | None
    observed_at: str
    ingested_at: str | None
    # content
    title: str
    body: str
    content_type: str
    attachment_references: tuple[AttachmentRef, ...]
    # enrichment (pipeline-owned; connectors leave empty and may set hints['domain_hints'])
    domain_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    topic_ids: tuple[str, ...]
    source_root_id: str | None
    # governance
    permissions: Permissions
    sensitivity: str
    retention_policy: RetentionPolicy
    content_hash: str
    deletion_status: str
    # ordering and provenance
    order_key: str
    root_known: bool = True
    root_method: str = "content"
    derived_from: tuple[DerivedFrom, ...] = ()
    links: tuple[str, ...] = ()
    hints: dict[str, Any] = field(default_factory=dict)

    # ---- derived identity
    @property
    def record_key(self) -> str:
        return record_key(self.tenant_id, self.holder_id, self.source_app, self.source_account_id, self.source_object_type, self.source_object_id)

    @property
    def record_id(self) -> str:
        return record_id_for(self.record_key)

    @property
    def object_key(self) -> str:
        return object_key(self.tenant_id, self.source_app, self.source_account_id, self.source_object_type, self.source_object_id)

    @property
    def event_key(self) -> str:
        return event_key(self.record_key, self.kind, self.source_version, self.content_hash, self.deletion_status, self.order_key)

    @property
    def version_key(self) -> str:
        return version_key(self.record_key, self.source_version, self.content_hash)

    @property
    def metadata_hash(self) -> str:
        return metadata_hash(labels=self.hints.get("labels") or (), state=self.hints.get("state"), participants=self.participant_ids,
                             permissions=self.permissions, container_name=self.hints.get("container_name"))

    def conversation_record_id(self) -> str | None:
        if not self.conversation_id:
            return None
        return record_id_for(record_key(self.tenant_id, self.holder_id, self.source_app, self.source_account_id, "conversation",
                                        self.conversation_id))

    def identity(self) -> dict[str, str]:
        return {"source_app": self.source_app, "source_account_id": self.source_account_id, "source_object_type": self.source_object_type,
                "source_object_id": self.source_object_id, "source_version": self.source_version}

    # ---- construction
    @classmethod
    def create(cls, *, kind: str, tenant_id: str, holder_id: str, connector_id: str, source_app: str, source_account_id: str,
               source_object_type: str, source_object_id: str, observed_at: str, source_version: str = "",
               source_event_id: str | None = None, conversation_id: str | None = None, thread_id: str | None = None,
               parent_message_id: str | None = None, author_id: str | None = None, participant_ids: Iterable[str] = (),
               created_at: Any = None, updated_at: Any = None, title: str = "", body: str = "", content_type: str = "text/plain",
               attachment_references: Iterable[AttachmentRef] = (), permissions: Permissions | None = None,
               sensitivity: str = "internal", retention_policy: RetentionPolicy | None = None, deletion_status: str | None = None,
               derived_from: Iterable[DerivedFrom] = (), links: Iterable[str] = (), hints: Mapping[str, Any] | None = None,
               tiebreak: str | None = None, resolve_explicit: Callable[[DerivedFrom], str | None] | None = None) -> "CanonicalEvent":
        """Build a v1 event applying §4: canonical body (own text only), content hash, order key and root."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        created = normalize_ts(created_at)
        updated = normalize_ts(updated_at)
        observed = normalize_ts(observed_at) or observed_at
        atts = tuple(attachment_references)
        derived = tuple(derived_from)
        hints = dict(hints or {})
        if kind in ("deletion", "redaction"):
            text, root = "", RootResult(None, False, "unknown", "")
            status = deletion_status or ("deleted_at_source" if kind == "deletion" else "redacted")
        else:
            root = compute_root(title, body, content_type, derived_from=derived, hints=hints, resolve_explicit=resolve_explicit)
            text = root.own_text
            status = deletion_status or "live"
            if root.quoted:
                hints.setdefault("quoted_segments", len(root.quoted))
            if root.forwarded:
                hints.setdefault("forwarded_segments", len(root.forwarded))
        title_c = canonical_body(title).replace("\n", " ").strip()
        chash = content_hash(title_c, text, "text/plain" if content_type.startswith("text/html") else content_type, atts)
        okey = make_order_key(updated_at=updated, created_at=created, observed_at=observed, tiebreak=tiebreak or chash[4:20])
        return cls(
            schema_version=SCHEMA_VERSION, kind=kind, tenant_id=tenant_id, holder_id=holder_id, connector_id=connector_id,
            source_app=source_app, source_account_id=source_account_id, source_object_type=source_object_type,
            source_object_id=str(source_object_id), source_version=str(source_version or ""), source_event_id=source_event_id,
            conversation_id=conversation_id, thread_id=thread_id, parent_message_id=parent_message_id, author_id=author_id,
            participant_ids=tuple(dict.fromkeys(str(p) for p in participant_ids if p)), created_at=created, updated_at=updated,
            observed_at=observed, ingested_at=None, title=title_c, body=text,
            content_type="text/plain" if content_type.startswith("text/html") else content_type, attachment_references=atts,
            domain_ids=(), entity_ids=(), topic_ids=(), source_root_id=root.root, permissions=permissions or Permissions(),
            sensitivity=sensitivity if sensitivity in SENSITIVITIES else "internal", retention_policy=retention_policy or RetentionPolicy(),
            content_hash=chash, deletion_status=status, order_key=okey, root_known=root.root_known, root_method=root.method,
            derived_from=derived, links=tuple(links), hints=hints)

    def with_(self, **changes: Any) -> "CanonicalEvent":
        return replace(self, **changes)

    def validate(self) -> list[str]:
        """Contract problems (an event with problems is counted as a normalize error and never stored)."""
        p: list[str] = []
        if self.schema_version != SCHEMA_VERSION:
            p.append("schema_version")
        if self.kind not in EVENT_KINDS:
            p.append("kind")
        for name in ("tenant_id", "holder_id", "connector_id", "source_app", "source_account_id", "source_object_type", "source_object_id"):
            if not getattr(self, name):
                p.append(name)
        if self.deletion_status not in DELETION_STATUSES:
            p.append("deletion_status")
        if self.kind in CONTENT_KINDS and not (self.body.strip() or self.title.strip()):
            p.append("empty")
        if self.kind in ("deletion", "redaction") and self.body:
            p.append("deletion_with_body")
        if not self.order_key or "|" not in self.order_key:
            p.append("order_key")
        return p

    # ---- serialization (queue payloads)
    def to_payload(self) -> dict[str, Any]:
        d = asdict(self)
        d["permissions"] = self.permissions.to_dict()
        return d

    @classmethod
    def from_payload(cls, d: Mapping[str, Any]) -> "CanonicalEvent":
        d = upcast(dict(d))
        kw = {f.name: d.get(f.name) for f in fields(cls) if f.name in d}
        kw["permissions"] = Permissions.from_dict(d.get("permissions"))
        kw["retention_policy"] = RetentionPolicy(**(d.get("retention_policy") or {}))
        kw["attachment_references"] = tuple(AttachmentRef(**a) for a in d.get("attachment_references") or ())
        kw["derived_from"] = tuple(DerivedFrom(**x) for x in d.get("derived_from") or ())
        for name in ("participant_ids", "domain_ids", "entity_ids", "topic_ids", "links"):
            kw[name] = tuple(d.get(name) or ())
        kw["hints"] = dict(d.get("hints") or {})
        return cls(**kw)


# ---------------------------------------------------------------------------------------------- schema evolution
UPCASTERS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}   # from_version -> fn producing from_version + 1


def upcast(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate an older queue payload in memory, so a dead letter never strands because of a schema bump."""
    v = int(payload.get("schema_version") or 1)
    while v < SCHEMA_VERSION:
        payload = UPCASTERS[v](payload)
        v += 1
        payload["schema_version"] = v
    if v > SCHEMA_VERSION:
        raise ValueError(f"event schema {v} is newer than this code ({SCHEMA_VERSION})")
    return payload


def encode_payload(ev: CanonicalEvent) -> bytes:
    return zlib.compress(canonical_json(ev.to_payload()).encode("utf-8"))


def decode_payload(blob: bytes) -> CanonicalEvent:
    return CanonicalEvent.from_payload(json.loads(zlib.decompress(blob).decode("utf-8")))
